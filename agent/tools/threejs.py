"""Three.js 3D Viewer tools (2), deferred behind ``tool_search``.

Scenes are described as JSON and compiled into a self-contained HTML page that
imports three.js from a CDN via an import map. The scene spec is validated
before rendering so a malformed vector produces a precise error instead of a
blank canvas.
"""
from __future__ import annotations

import html
import json
from typing import Any

from agent.tools.base import ToolContext, ToolResult, tool

THREE_VERSION = "0.160.0"
MAX_OBJECTS = 200
SHAPES = {"box", "sphere", "plane", "cylinder", "cone", "torus", "torusKnot"}
LIGHT_TYPES = {"ambient", "directional", "point", "hemisphere"}

GUIDE = {
    "basics": (
        "A scene needs a camera, at least one light and objects. Coordinates are "
        "three.js's right-handed system: +x right, +y up, +z toward the viewer."
    ),
    "objects": (
        "Supported types: box, sphere, plane, cylinder, cone, torus, torusKnot. "
        "Each takes position/rotation (radians) and optional scale/color."
    ),
    "lighting": (
        "Use one ambient light plus one directional light for a predictable look. "
        "Directional light positions act like the sun."
    ),
    "materials": (
        "Materials are MeshStandardMaterial: color plus optional roughness and "
        "metalness. Roughness 0.8 and metalness 0.1 reads well on screen."
    ),
    "animation": (
        "Set animate=true to slowly rotate the whole group; anything fancier needs "
        "a custom scene, which this tool does not generate."
    ),
}


@tool(
    group="Three.js 3D Viewer",
    name="mcp__Three_js_3D_Viewer__learn_threejs",
    requires_tool_search=True,
    tags=("threejs", "learn", "guide", "3d", "docs"),
)
def mcp__Three_js_3D_Viewer__learn_threejs(topic: str = "basics") -> ToolResult:
    """Explain three.js concepts and the scene format used here.

    Args:
        topic: One of basics, objects, lighting, materials, animation.
    """
    key = topic.strip().lower() or "basics"
    if key not in GUIDE:
        return ToolResult.error(
            f"unknown topic {topic!r}", error="unknown_topic", topics=sorted(GUIDE)
        )
    body = (
        f"# three.js: {key}\n\n{GUIDE[key]}\n\n"
        "Scene format accepted by show_threejs_scene:\n"
        '{"background": "#101014", "camera": {"position": [3, 3, 6], "lookAt": [0, 0, 0]},\n'
        ' "lights": [{"type": "ambient", "intensity": 0.6},\n'
        '            {"type": "directional", "intensity": 1.2, "position": [5, 8, 4]}],\n'
        ' "objects": [{"type": "box", "size": [1, 1, 1], "position": [0, 0.5, 0],\n'
        '              "color": "#4f8cff", "roughness": 0.6, "metalness": 0.1}],\n'
        ' "animate": true}\n'
    )
    return ToolResult.text(body, topic=key, topics=sorted(GUIDE))


def _vec3(value: Any, field: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{field} must be an array of 3 numbers, got {value!r}")
    try:
        return [float(v) for v in value]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain numbers, got {value!r}") from exc


def validate_scene(scene: dict) -> dict:
    """Normalise and validate a scene spec; raises ValueError with a precise reason."""
    if not isinstance(scene, dict):
        raise ValueError("scene must be an object")
    objects = scene.get("objects") or []
    if not isinstance(objects, list) or not objects:
        raise ValueError("scene.objects must be a non-empty array")
    if len(objects) > MAX_OBJECTS:
        raise ValueError(f"scene.objects is capped at {MAX_OBJECTS} entries, got {len(objects)}")

    normalised_objects = []
    for index, item in enumerate(objects, 1):
        if not isinstance(item, dict):
            raise ValueError(f"object #{index} must be an object")
        shape = str(item.get("type", "")).strip()
        if shape not in SHAPES:
            raise ValueError(
                f"object #{index}: unsupported type {shape!r}; use one of {sorted(SHAPES)}"
            )
        entry = {
            "type": shape,
            "position": _vec3(item.get("position", [0, 0, 0]), f"object #{index}.position"),
            "rotation": _vec3(item.get("rotation", [0, 0, 0]), f"object #{index}.rotation"),
            "scale": _vec3(item.get("scale", [1, 1, 1]), f"object #{index}.scale"),
            "size": _vec3(item.get("size", [1, 1, 1]), f"object #{index}.size"),
            "color": str(item.get("color", "#7dd3fc")),
            "roughness": float(item.get("roughness", 0.6)),
            "metalness": float(item.get("metalness", 0.1)),
        }
        normalised_objects.append(entry)

    lights = []
    for index, item in enumerate(scene.get("lights") or [], 1):
        if not isinstance(item, dict):
            raise ValueError(f"light #{index} must be an object")
        kind = str(item.get("type", "ambient")).strip()
        if kind not in LIGHT_TYPES:
            raise ValueError(f"light #{index}: unsupported type {kind!r}; use {sorted(LIGHT_TYPES)}")
        lights.append(
            {
                "type": kind,
                "intensity": float(item.get("intensity", 1.0)),
                "position": _vec3(item.get("position", [5, 8, 4]), f"light #{index}.position"),
                "color": str(item.get("color", "#ffffff")),
            }
        )

    camera = scene.get("camera") or {}
    return {
        "background": str(scene.get("background", "#0b1020")),
        "animate": bool(scene.get("animate", True)),
        "camera": {
            "position": _vec3(camera.get("position", [4, 3, 6]), "camera.position"),
            "lookAt": _vec3(camera.get("lookAt", [0, 0, 0]), "camera.lookAt"),
        },
        "lights": lights,
        "objects": normalised_objects,
    }


def build_scene_html(scene: dict, title: str) -> str:
    payload = json.dumps(scene)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>html,body{{margin:0;height:100%;background:{html.escape(scene['background'])}}}
canvas{{display:block}}#hud{{position:fixed;left:12px;bottom:12px;color:#e5e7eb;
font:12px system-ui,sans-serif;opacity:.75}}</style>
<script type="importmap">
{{"imports": {{"three": "https://unpkg.com/three@{THREE_VERSION}/build/three.module.js"}}}}
</script></head>
<body><div id="hud">{html.escape(title)}</div>
<script type="module">
import * as THREE from 'three';
const spec = {payload};
const scene = new THREE.Scene();
scene.background = new THREE.Color(spec.background);
const camera = new THREE.PerspectiveCamera(50, innerWidth / innerHeight, 0.1, 1000);
camera.position.set(...spec.camera.position);
camera.lookAt(new THREE.Vector3(...spec.camera.lookAt));
const renderer = new THREE.WebGLRenderer({{antialias: true}});
renderer.setSize(innerWidth, innerHeight);
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
document.body.appendChild(renderer.domElement);

for (const light of spec.lights) {{
  let source;
  if (light.type === 'ambient') source = new THREE.AmbientLight(light.color, light.intensity);
  else if (light.type === 'hemisphere') source = new THREE.HemisphereLight(light.color, 0x222233, light.intensity);
  else if (light.type === 'directional') source = new THREE.DirectionalLight(light.color, light.intensity);
  else source = new THREE.PointLight(light.color, light.intensity);
  if (light.position) source.position.set(...light.position);
  scene.add(source);
}}

const group = new THREE.Group();
const [w, h, d] = [1, 1, 1];
for (const item of spec.objects) {{
  let geometry;
  const [sx, sy, sz] = item.size;
  switch (item.type) {{
    case 'box': geometry = new THREE.BoxGeometry(sx, sy, sz); break;
    case 'sphere': geometry = new THREE.SphereGeometry(sx / 2, 32, 16); break;
    case 'plane': geometry = new THREE.PlaneGeometry(sx, sy); break;
    case 'cylinder': geometry = new THREE.CylinderGeometry(sx / 2, sx / 2, sy, 32); break;
    case 'cone': geometry = new THREE.ConeGeometry(sx / 2, sy, 32); break;
    case 'torus': geometry = new THREE.TorusGeometry(sx / 2, sy / 4, 16, 64); break;
    default: geometry = new THREE.TorusKnotGeometry(sx / 3, sy / 9, 80, 16);
  }}
  const material = new THREE.MeshStandardMaterial({{
    color: new THREE.Color(item.color),
    roughness: item.roughness,
    metalness: item.metalness,
  }});
  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.set(...item.position);
  mesh.rotation.set(...item.rotation);
  mesh.scale.set(...item.scale);
  group.add(mesh);
}}
scene.add(group);

function resize() {{
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
}}
addEventListener('resize', resize);

let t = 0;
renderer.setAnimationLoop(() => {{
  if (spec.animate) {{ t += 0.004; group.rotation.y = t; }}
  renderer.render(scene, camera);
}});
</script></body></html>"""


@tool(
    group="Three.js 3D Viewer",
    name="mcp__Three_js_3D_Viewer__show_threejs_scene",
    requires_tool_search=True,
    tags=("threejs", "scene", "3d", "render", "visualise"),
)
def mcp__Three_js_3D_Viewer__show_threejs_scene(
    ctx: ToolContext,
    scene: dict,
    title: str = "Three.js Scene",
) -> ToolResult:
    """Render a validated three.js scene to an HTML artifact.

    Call learn_threejs first for the accepted scene format.

    Args:
        scene: Scene spec with objects, optional lights, camera, background and animate.
        title: Page title, also used for the file name.
    """
    try:
        normalised = validate_scene(scene)
    except ValueError as exc:
        return ToolResult.error(str(exc), error="invalid_scene")
    page = build_scene_html(normalised, title)
    info = ctx.save_artifact(page, name=title, ext="html", mime="text/html")
    ctx.emit_event({"type": "threejs_scene", "title": title, "path": info["path"]})
    return ToolResult.text(
        f"rendered three.js scene with {len(normalised['objects'])} object(s) to {info['path']}",
        **info,
        objects=len(normalised["objects"]),
        lights=len(normalised["lights"]),
    )


__all__ = [
    "mcp__Three_js_3D_Viewer__learn_threejs",
    "mcp__Three_js_3D_Viewer__show_threejs_scene",
    "validate_scene",
    "build_scene_html",
]
