import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { bounds, type Room } from '../domain/room';
import { buildRoomGroup, disposeGroup } from '../scene/buildRoom';

type Props = { room: Room; furniture: boolean; cutaway: boolean; selected: string; onSelect: (id: string) => void; resetKey: number };
export default function RoomViewer({ room, furniture, cutaway, selected, onSelect, resetKey }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const pose = useRef<{ roomId: string; resetKey: number; position: THREE.Vector3; target: THREE.Vector3 } | null>(null);
  const onSelectRef = useRef(onSelect); onSelectRef.current = onSelect;
  const [error, setError] = useState('');
  useEffect(() => {
    if (!host.current) return;
    const container = host.current;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); } catch { setError('3D is unavailable on this device. Use the floorplan view.'); return; }
    setError('');
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2)); renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.setClearColor('#f3f3ed', 1); renderer.outputColorSpace = THREE.SRGBColorSpace; renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.3;
    renderer.domElement.setAttribute('aria-label', 'Interactive 3D room. Drag to orbit, scroll to zoom, click a wall or object to select.');
    renderer.domElement.setAttribute('role', 'img'); container.appendChild(renderer.domElement);
    const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(38, 1, 0.01, 1000);
    const b = bounds(room), cx = (b.minX + b.maxX) / 2, cz = (b.minZ + b.maxZ) / 2, span = Math.max(b.width, b.depth, 3);
    camera.position.set(cx + span * 1.12, span * 1.0, cz + span * 1.35);
    const controls = new OrbitControls(camera, renderer.domElement); controls.target.set(cx, 0.5, cz); controls.enableDamping = true; controls.maxPolarAngle = Math.PI * 0.48; controls.minDistance = 1; controls.maxDistance = span * 6; controls.update();
    if (pose.current?.roomId === room.id && pose.current.resetKey === resetKey) {
      camera.position.copy(pose.current.position); controls.target.copy(pose.current.target); controls.update();
    }
    scene.add(new THREE.HemisphereLight('#ffffff', '#a6ad99', 2.8));
    const sun = new THREE.DirectionalLight('#fff9e9', 3); sun.position.set(cx - 3, 9, cz + 2); sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048); sun.shadow.camera.left = -span; sun.shadow.camera.right = span; sun.shadow.camera.top = span; sun.shadow.camera.bottom = -span; sun.shadow.bias = -0.001; sun.target.position.set(cx, 0, cz); scene.add(sun, sun.target);
    const group = buildRoomGroup(room, { furniture, selected }); scene.add(group);
    const grid = new THREE.GridHelper(Math.ceil(span * 4), Math.ceil(span * 4) * 2, '#d7dcd2', '#e1e4db'); grid.position.set(cx, -0.04, cz); scene.add(grid);
    const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2(); let start = { x: 0, y: 0 };
    const down = (e: PointerEvent) => { start = { x: e.clientX, y: e.clientY }; };
    const up = (e: PointerEvent) => {
      if (Math.hypot(start.x - e.clientX, start.y - e.clientY) > 5) return;
      const rect = renderer.domElement.getBoundingClientRect(); pointer.set((e.clientX - rect.left) / rect.width * 2 - 1, -(e.clientY - rect.top) / rect.height * 2 + 1); raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObject(group, true).find(h => h.object.userData.elementId && (!(h.object instanceof THREE.Mesh) || !Array.isArray(h.object.material) && h.object.material.opacity > 0.2));
      if (hit) onSelectRef.current(hit.object.userData.elementId);
    };
    renderer.domElement.addEventListener('pointerdown', down); renderer.domElement.addEventListener('pointerup', up);
    const resize = () => { const width = container.clientWidth, height = container.clientHeight; renderer.setSize(width, height); camera.aspect = width / Math.max(height, 1); camera.updateProjectionMatrix(); };
    const observer = new ResizeObserver(resize); observer.observe(container); resize();
    let frame = 0;
    const draw = () => {
      frame = requestAnimationFrame(draw); controls.update();
      for (const child of group.children) {
        const wall = room.walls.find(w => w.id === child.userData.wallId); if (!wall) continue;
        const mx = (wall.start.x + wall.end.x) / 2, mz = (wall.start.z + wall.end.z) / 2;
        const near = (camera.position.x - mx) * (mx - cx) + (camera.position.z - mz) * (mz - cz) > 0;
        child.traverse(o => { if (!(o instanceof THREE.Mesh)) return; const mats = Array.isArray(o.material) ? o.material : [o.material]; for (const m of mats) {
          if (m.userData.originalOpacity === undefined) m.userData.originalOpacity = m.opacity;
          m.opacity = cutaway && near ? (wall.id === selected ? 0.3 : 0.08) : m.userData.originalOpacity;
          m.transparent = m.opacity < 1; m.depthWrite = m.opacity > 0.5;
        } });
      }
      renderer.render(scene, camera);
    }; draw();
    return () => { pose.current = { roomId: room.id, resetKey, position: camera.position.clone(), target: controls.target.clone() }; cancelAnimationFrame(frame); observer.disconnect(); controls.dispose(); renderer.domElement.removeEventListener('pointerdown', down); renderer.domElement.removeEventListener('pointerup', up); disposeGroup(scene); renderer.dispose(); container.removeChild(renderer.domElement); };
  }, [room, furniture, cutaway, selected, resetKey]);
  return <div ref={host} className="three-view">{error ? <p className="viewer-error" role="alert">{error}</p> : null}</div>;
}
