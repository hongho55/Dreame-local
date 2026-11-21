"""REST backend for HA; no standalone web pages or bundled UI assets."""
from __future__ import annotations

import base64
import binascii
import json
import logging
import math
import re
import struct
import zlib
from pathlib import Path
from typing import Any

from aiohttp import web

import events
from device import Device, MODE, SUCTION, WATER

log = logging.getLogger("web")


@web.middleware
async def no_cache(request: web.Request, handler):
    response = await handler(request)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


class Web:
    def __init__(
        self,
        device: Device,
        port: int = 8099,
        data_dir: Path | None = None,
        clients_fn=None,
        version: str = "",
    ) -> None:
        self.dev = device
        self.clients_fn = clients_fn
        self.version = version
        self.port = port
        self.data_dir = data_dir or Path("/data")
        self.app = web.Application(
            client_max_size=512 * 1024 * 1024,
            middlewares=[no_cache],
        )
        self.app.add_routes(
            [
                web.get("/api/state", self.state),
                web.get("/api/uploads", self.uploads),
                web.get("/api/diagnostics", self.diagnostics),
                web.get("/api/mapcheck", self.mapcheck),
                web.get("/api/rooms", self.rooms_data),
                web.get("/api/map.png", self.map_png),
                web.post("/api/rooms/action", self.room_action),
                web.post("/api/command", self.command),
                web.put("/upload/{name:.*}", self.upload),
            ]
        )

    async def start(self) -> None:
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port)
        await site.start()
        log.info("REST-API auf :%d", self.port)


    async def state(self, request: web.Request) -> web.Response:
        snap = self.dev.snapshot()
        snap["version"] = self.version
        if self.clients_fn:
            # Online-Status direkt aus den aktiven MQTT-Sessions ableiten,
            # damit eine verspaetete alte Session ihn nicht dauerhaft kippt.
            clients = self.clients_fn()
            snap["online"] = any(
                str(c.get("client_id", "")).startswith("device_") for c in clients
            )
            snap["clients"] = clients
        return web.json_response(snap)

    async def uploads(self, request: web.Request) -> web.Response:
        root = self.data_dir / "uploads"
        files = []
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file():
                    st = p.stat()
                    files.append({"name": str(p.relative_to(root)), "size": st.st_size,
                                  "mtime": int(st.st_mtime)})
        files.sort(key=lambda x: x["mtime"], reverse=True)
        return web.json_response({"uploads": files, "log": str(self.data_dir / "api.log")})

    async def diagnostics(self, request: web.Request) -> web.Response:
        root = self.data_dir / "uploads"
        uploads = []
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file():
                    st = p.stat()
                    uploads.append({"name": str(p.relative_to(root)), "size": st.st_size,
                                    "mtime": int(st.st_mtime)})
        uploads.sort(key=lambda x: x["mtime"], reverse=True)
        clients = self.clients_fn() if self.clients_fn else []
        return web.json_response({"clients": clients, "events": events.all(), "uploads": uploads})

    async def mapcheck(self, request: web.Request) -> web.Response:
        """Prueft den zuletzt hochgeladenen Kartenblob (Format/Header)."""
        root = self.data_dir / "uploads"
        name = request.query.get("name") or f"ali_dreame/{self.dev.uid}/{self.dev.did}/9"
        target = root / name
        if not target.is_file():
            candidates = (
                [p for p in root.rglob("*") if p.is_file() and not p.name.startswith("__")]
                if root.exists()
                else []
            )
            if not candidates:
                return web.json_response({"ok": False, "error": "업로드된 파일이 없습니다"})
            target = max(candidates, key=lambda p: p.stat().st_mtime)
            name = str(target.relative_to(root))

        result: dict[str, Any] = {
            "ok": False,
            "name": name,
            "size": target.stat().st_size,
        }
        try:
            data = target.read_bytes()
            text = data.decode("utf-8", "replace").strip()
            result["text_len"] = len(text)
            result["head"] = text[:60]
            key = None
            if "," in text:
                text, key = text.split(",", 1)
                result["embedded_key"] = True
            raw_b64 = text.replace("_", "/").replace("-", "+")
            while len(raw_b64) % 4:
                raw_b64 += "="
            raw = base64.b64decode(raw_b64, validate=False)
            result["decoded_len"] = len(raw)
            result["magic"] = raw[:4].hex()
            try:
                plain = zlib.decompress(raw)
                result["zlib"] = True
            except Exception:
                plain = raw
                result["zlib"] = False
            result["plain_len"] = len(plain)
            if result["zlib"] and len(plain) >= 27:
                result["map_id"] = int.from_bytes(plain[0:2], "little")
                result["frame_id"] = int.from_bytes(plain[2:4], "little")
                result["frame_type"] = plain[4]
                width = int.from_bytes(plain[19:21], "little")
                height = int.from_bytes(plain[21:23], "little")
                result["width"] = width
                result["height"] = height
                image_size = 27 + width * height
                result["image_size"] = image_size
                if len(plain) >= image_size:
                    try:
                        meta = json.loads(plain[image_size:].decode("utf-8"))
                        result["json_keys"] = sorted(meta.keys())
                        result["json_timestamp_ms"] = meta.get("timestamp_ms")
                    except Exception as exc:
                        result["json_error"] = str(exc)
                result["ok"] = True
            elif not result["zlib"]:
                result["error"] = (
                    "zlib 헤더가 없습니다. 암호화된 파일이거나 "
                    "지도 파일이 아닐 수 있습니다 (식별값 %s)" % result["magic"]
                )
        except Exception as exc:
            result["error"] = str(exc)
        return web.json_response(result)

    # ---------------------------------------------------------- Raum-Editor
    @staticmethod
    def _decode_map_text(text: str):
        text = text.strip()
        if not text:
            return None
        if text.startswith("{"):
            try:
                entries = json.loads(text).get("mapstr")
            except (ValueError, TypeError):
                return None
            if not isinstance(entries, list):
                return None
            for entry in entries:
                blob = entry.get("map") if isinstance(entry, dict) else None
                # Accept the same map-list wrapper as the cloud API, not
                # recursively nested wrappers or unrelated uploaded JSON.
                if isinstance(blob, str) and not blob.lstrip().startswith("{"):
                    decoded = Web._decode_map_text(blob)
                    if decoded:
                        return decoded
            return None
        if "," in text:
            text = text.split(",", 1)[0]
        t = text.replace("_", "/").replace("-", "+")
        t += "=" * (-len(t) % 4)
        try:
            raw = zlib.decompress(base64.b64decode(t, validate=False))
        except Exception:
            return None
        if len(raw) < 27:
            return None
        width = int.from_bytes(raw[19:21], "little")
        height = int.from_bytes(raw[21:23], "little")
        image_end = 27 + width * height
        if width <= 0 or height <= 0 or len(raw) < image_end:
            return None
        try:
            info = json.loads(raw[image_end:].decode("utf-8"))
        except Exception:
            return None
        return raw, width, height, info

    def _load_map(self):
        root = self.data_dir / "uploads" / f"ali_dreame/{self.dev.uid}/{self.dev.did}"
        if not self.dev.uid:
            # UID can be empty after a server restart until the next dev_start.
            # Read only an unambiguous cache for this exact device; never
            # change identity or select another device/account by freshness.
            base = self.data_dir / "uploads" / "ali_dreame"
            candidates = [p for p in base.glob("*/*")
                          if p.name == str(self.dev.did) and p.is_dir()]
            if len(candidates) == 1:
                root = candidates[0]
        best = None
        for name in ("9", "9.frame"):
            path = root / name
            try:
                if not path.is_file():
                    continue
                decoded = self._decode_map_text(path.read_text(errors="replace"))
                if not decoded:
                    continue
                raw, width, height, info = decoded
                item = {
                    "raw": raw, "width": width, "height": height, "info": info,
                    "map_id": int.from_bytes(raw[0:2], "little"),
                    "grid": int.from_bytes(raw[17:19], "little") or 50,
                    "origin": info.get("origin") or [
                        int.from_bytes(raw[23:25], "little", signed=True),
                        int.from_bytes(raw[25:27], "little", signed=True),
                    ],
                    "name": name, "mtime": path.stat().st_mtime,
                }
                if best is None or item["mtime"] > best["mtime"]:
                    best = item
            except Exception:
                continue
        if best is None:
            live = self.dev.props.get("6.1")
            if isinstance(live, str) and live:
                decoded = self._decode_map_text(live)
                if decoded:
                    raw, width, height, info = decoded
                    best = {
                        "raw": raw, "width": width, "height": height, "info": info,
                        "map_id": int.from_bytes(raw[0:2], "little"),
                        "grid": int.from_bytes(raw[17:19], "little") or 50,
                        "origin": info.get("origin") or [0, 0],
                        "name": "live", "mtime": 0,
                    }
        return best

    def _room_names(self) -> dict[str, str]:
        try:
            data = json.loads((self.data_dir / "room_names.json").read_text())
            return {str(k): str(v) for k, v in data.items()}
        except Exception:
            return {}

    def _save_room_name(self, room_id: int, name: str) -> None:
        names = self._room_names()
        names[str(room_id)] = name
        try:
            (self.data_dir / "room_names.json").write_text(json.dumps(names, ensure_ascii=False))
        except OSError:
            pass

    def _room_list(self, m):
        raw, width, height = m["raw"], m["width"], m["height"]
        origin_x, origin_y = m["origin"][0], m["origin"][1]
        grid = m["grid"] or 50
        names = self._room_names()
        img = raw[27:27 + width * height]
        bboxes = {}
        for y in range(height):
            for x in range(width):
                v = img[y * width + x]
                if 0 < v < 64:
                    b = bboxes.get(v)
                    if b is None:
                        bboxes[v] = [x, y, x, y]
                    else:
                        if x < b[0]: b[0] = x
                        if y < b[1]: b[1] = y
                        if x > b[2]: b[2] = x
                        if y > b[3]: b[3] = y
        palette = ['#4c78a8', '#f58518', '#54a24b', '#b279a2', '#72b7b2', '#e45756', '#9d755d', '#bab0ac']
        rooms = []
        for rid, b in sorted(bboxes.items()):
            # Rohbild ist von unten nach oben gespeichert; die Integration
            # rendert mit y = height - 1 - raw_y. Fuer die Anzeige und die
            # Klickkoordinaten muessen wir genauso spiegeln.
            display_bbox = [b[0], height - 1 - b[3], b[2], height - 1 - b[1]]
            rooms.append({
                "id": rid,
                "name": names.get(str(rid)) or f"방 {rid}",
                "color": palette[(rid - 1) % len(palette)],
                "bbox": display_bbox,
                "center": [(display_bbox[0] + display_bbox[2]) / 2, (display_bbox[1] + display_bbox[3]) / 2],
                "map_bbox": [
                    origin_x + b[0] * grid, origin_y + b[1] * grid,
                    origin_x + b[2] * grid, origin_y + b[3] * grid,
                ],
            })
        return rooms

    def _map_png(self, m) -> bytes:
        raw, width, height = m["raw"], m["width"], m["height"]
        img = raw[27:27 + width * height]
        palette = ['#4c78a8', '#f58518', '#54a24b', '#b279a2', '#72b7b2', '#e45756', '#9d755d', '#bab0ac']

        def rgb(hex_color):
            hex_color = hex_color.lstrip('#')
            return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))

        room_colors = [rgb(c) for c in palette]
        colors = {
            0: (250, 250, 250), 255: (45, 45, 45), 254: (43, 108, 176),
            253: (160, 174, 192), 252: (160, 160, 160), 251: (210, 80, 80),
            250: (235, 180, 100), 249: (72, 187, 120), 248: (160, 220, 160),
        }
        scale = 4
        rows = []
        for y in range(height * scale):
            row = bytearray([0])
            raw_y = height - 1 - (y // scale)
            for x in range(width * scale):
                v = img[raw_y * width + (x // scale)]
                if 0 < v < 64:
                    c = room_colors[(v - 1) % len(room_colors)]
                elif 100 < v < 200:
                    c = room_colors[(v - 101) % len(room_colors)]
                else:
                    c = colors.get(v, (120, 120, 120))
                row += bytes(c)
            rows.append(bytes(row))
        W, H = width * scale, height * scale

        def chunk(tag, data):
            return struct.pack("!I", len(data)) + tag + data + struct.pack(
                "!I", binascii.crc32(tag + data) & 0xFFFFFFFF
            )

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack("!IIBBBBB", W, H, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + chunk(b"IEND", b"")
        )


    async def rooms_data(self, request: web.Request) -> web.Response:
        m = self._load_map()
        if not m:
            return web.json_response({"ok": False, "error": "저장된 지도 데이터를 찾을 수 없습니다"})
        origin_x, origin_y = m["origin"][0], m["origin"][1]
        grid = m["grid"] or 50
        robot = None
        live = self.dev.props.get("6.1")
        if isinstance(live, str) and live:
            decoded = self._decode_map_text(live)
            if decoded:
                lraw, _lw, _lh, _linfo = decoded
                x = int.from_bytes(lraw[5:7], "little", signed=True)
                y = int.from_bytes(lraw[7:9], "little", signed=True)
                a = int.from_bytes(lraw[9:11], "little", signed=True)
                if x != 32767 and y != 32767:
                    robot = {
                        "x": x, "y": y, "a": a,
                        "px": (x - origin_x) / grid,
                        "py": m["height"] - 1 - (y - origin_y) / grid,
                    }
        path = []
        raw_path = getattr(self.dev, "map_path", "")
        if isinstance(raw_path, str) and raw_path:
            current = (0, 0)
            for match in re.finditer(r"([MWSLl])(-?\d+),(-?\d+)", raw_path):
                operator = match.group(1)
                x = int(match.group(2))
                y = int(match.group(3))
                if operator == "L":
                    current = (current[0] + x, current[1] + y)
                else:
                    current = (x, y)
                path.append([
                    (current[0] - origin_x) / grid,
                    m["height"] - 1 - (current[1] - origin_y) / grid,
                ])
            if len(path) > 4000:
                path = path[-4000:]
        return web.json_response({
            "ok": True,
            "map_id": m["map_id"],
            "width": m["width"],
            "height": m["height"],
            "origin": m["origin"],
            "grid": m["grid"],
            "source": m["name"],
            "mtime": int(m["mtime"]),
            "rooms": self._room_list(m),
            "zones": m["info"].get("vw") or {},
            "robot": robot,
            "path": path,
        })

    async def map_png(self, request: web.Request) -> web.Response:
        m = self._load_map()
        if not m:
            raise web.HTTPNotFound()
        return web.Response(body=self._map_png(m), content_type="image/png")

    async def room_action(self, request: web.Request) -> web.Response:
        body = await request.json()
        action = str(body.get("action") or "")
        try:
            if action == "request_map":
                frame = str(body.get("frame") or '{"frame_type":"I"}')
                result = await self.dev.raw_action(6, 1, [{"piid": 2, "value": frame}])
                events.add("map_request", result=result)
                return web.json_response({"ok": True, "result": result})
            if action == "fast_map":
                # START_CUSTOM mit Status 21 = Schnellkartierung
                result = await self.dev.raw_action(4, 1, [{"piid": 1, "value": 21}])
                events.add("fast_map", result=result)
                return web.json_response({"ok": True, "result": result})

            m = self._load_map()
            if not m:
                return web.json_response({"ok": False, "error": "불러온 지도가 없습니다"})
            map_id = int(body.get("map_id") or m["map_id"])

            if action == "clean":
                ids = [int(v) for v in (body.get("ids") or [])]
                if not ids:
                    raise ValueError("선택한 방이 없습니다")
                fan, water = self.dev.cleaning_settings()
                cleanlist = [[rid, 1, fan, water, 1] for rid in ids]
                payload = {"selects": cleanlist}
                params = {
                    "did": self.dev.did,
                    "siid": 4,
                    "aiid": 1,
                    "in": [
                        {"piid": 1, "value": 18},  # SEGMENT_CLEANING
                        {"piid": 10, "value": json.dumps(payload, separators=(",", ":"))},
                    ],
                }
                log.info("Raumreinigung %s", payload)
                await self.dev.ensure_saved_map()
                result = await self.dev.send("action", params, timeout=20)
                events.add("room_clean", ids=ids, result=result)
                if isinstance(result, dict) and result.get("code") not in (0, None):
                    return web.json_response({"ok": False, "error": str(result), "result": result})
                return web.json_response({"ok": True, "result": result})

            if action == "zone_clean":
                fan, water = self.dev.cleaning_settings()
                areas = []
                for area in (body.get("areas") or []):
                    coords = [int(round(float(v))) for v in area[:4]]
                    if len(coords) == 4:
                        areas.append([*coords, 1, fan, water])
                if not areas:
                    raise ValueError("선택한 청소 구역이 없습니다")
                payload = {"areas": areas}
                params = {
                    "did": self.dev.did,
                    "siid": 4,
                    "aiid": 1,
                    "in": [
                        {"piid": 1, "value": 19},  # ZONE_CLEANING
                        {"piid": 10, "value": json.dumps(payload, separators=(",", ":"))},
                    ],
                }
                log.info("Zonenreinigung %s", payload)
                await self.dev.ensure_saved_map()
                result = await self.dev.send("action", params, timeout=20)
                events.add("zone_clean", areas=areas, result=result)
                if isinstance(result, dict) and result.get("code") not in (0, None):
                    return web.json_response({"ok": False, "error": str(result), "result": result})
                return web.json_response({"ok": True, "result": result})

            if action == "backup_map":
                result = await self.dev.raw_action(6, 3, [{"piid": 4, "value": str(map_id)}])
                events.add("map_backup", map_id=map_id, result=result)
                return web.json_response({"ok": True, "result": result})

            if action == "rename":
                rid = int(body.get("id"))
                name = str(body.get("name") or "").strip()
                if not name:
                    raise ValueError("방 이름을 입력하세요")
                payload = {
                    "nsr": {str(rid): {
                        "name": base64.b64encode(name.encode("utf-8")).decode("ascii"),
                        "type": 0,
                        "index": 0,
                    }},
                    "mapid": map_id,
                }
            elif action == "merge":
                ids = [int(v) for v in (body.get("ids") or [])]
                if len(ids) != 2:
                    raise ValueError("방을 정확히 두 개 선택하세요")
                payload = {"msr": ids, "mapid": map_id}
            elif action == "split":
                rid = int(body.get("id"))
                line = [int(round(float(v))) for v in (body.get("line") or [])]
                if len(line) != 4:
                    raise ValueError("방을 나눌 경계선이 없습니다")
                payload = {"dsrid": line + [rid], "mapid": map_id}
            elif action == "zones":
                walls = [[int(round(float(v))) for v in wall[:4]] for wall in (body.get("walls") or [])]
                zones = [[int(round(float(v))) for v in zone[:4]] for zone in (body.get("zones") or [])]
                no_mops = [[int(round(float(v))) for v in zone[:4]] for zone in (body.get("no_mops") or [])]
                vw = {"line": walls, "rect": zones, "mop": no_mops}
                extras = body.get("extras") or {}
                if isinstance(extras, dict):
                    for key in ("nocpt", "addcpt"):
                        if isinstance(extras.get(key), list):
                            vw[key] = extras[key]
                payload = {"vw": vw}
            else:
                raise ValueError("알 수 없는 동작입니다")
            params = {
                "did": self.dev.did,
                "siid": 6,
                "aiid": 2,
                "in": [{"piid": 4, "value": json.dumps(payload, separators=(",", ":"))}],
            }
            log.info("Raum-Aktion %s: %s", action, payload)
            result = await self.dev.send("action", params, timeout=20)
            events.add("room_edit", action=action, map_id=map_id, result=result)
            if isinstance(result, dict) and result.get("code") not in (0, None):
                return web.json_response({"ok": False, "error": str(result), "result": result})
            if action == "rename":
                self._save_room_name(rid, name)
            return web.json_response({"ok": True, "result": result})
        except Exception as exc:
            log.warning("Raum-Aktion fehlgeschlagen: %s", exc)
            return web.json_response({"ok": False, "error": str(exc)})

    async def command(self, request: web.Request) -> web.Response:
        body: dict[str, Any] = await request.json()
        try:
            if "action" in body:
                action = str(body["action"])
                res = await self.dev.action("charge" if action == "return_to_base" else action)
            elif "method" in body:
                res = await self.dev.send(str(body["method"]), body.get("params"))
            elif "set" in body:
                prop = str(body["set"])
                value = body.get("value")
                if prop == "SUCTION_LEVEL" and isinstance(value, str):
                    value = SUCTION.get(value.upper(), value)
                elif prop == "WATER_VOLUME" and isinstance(value, str):
                    value = WATER.get(value.upper(), value)
                elif prop == "CLEANING_MODE" and isinstance(value, str):
                    value = MODE.get(value.upper(), value)
                if prop == "CLEANING_MODE":
                    res = await self.dev.set_cleaning_mode(value)
                elif prop == "WATER_VOLUME":
                    res = await self.dev.set_water_volume(value)
                elif prop == "WETNESS_LEVEL":
                    res = await self.dev.set_wetness_level(value)
                else:
                    res = await self.dev.set_property(prop, value)
            elif "get" in body:
                res = await self.dev.get_properties(list(body["get"]))
            else:
                return web.json_response({"ok": False, "error": "action|method|set|get 중 하나가 필요합니다"}, status=400)
            results = res if isinstance(res, list) else [res]
            failures = [r.get("code") for r in results if isinstance(r, dict) and r.get("code") not in (0, None)]
            if failures:
                return web.json_response({"ok": False, "error": f"로봇이 명령을 거절했습니다 (코드 {failures[0]})", "result": res})
            return web.json_response({"ok": True, "result": res})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    async def upload(self, request: web.Request) -> web.Response:
        name = request.match_info["name"].replace("..", "_")
        target = self.data_dir / "uploads" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with target.open("wb") as f:
            async for chunk in request.content.iter_chunked(65536):
                f.write(chunk)
                size += len(chunk)
        log.info("Upload gespeichert: %s (%d Bytes)", target, size)
        return web.Response(text="ok")
