"use client";
// Common Test 1B / Part 2 pictures. Drawn as simple SVG pictograms from checked, structured data: only fixed element
// kinds, poses and attributes (shade, pattern, shape, count, state, hair, what a person wears) and short plain-text
// labels exist - nothing from the material is injected as markup, and what an answer depends on is drawn explicitly.
// Two forms: picture_set (four panels, one per option) and labeled_scene (one picture, four things marked 1-4).
import type { ReactElement, ReactNode } from "react";
import type { Json } from "@/lib/api";

const W = 160, H = 120;
const INK = "#e5edf7", MUTE = "#8aa0b8", ACC = "#5eead4", BG = "#0b1324";
const SHADE: Record<string, string> = { white: "#f1f5f9", light: "#8aa0b8", dark: "#3b4a63" };
const PATTERNS = ["stripes", "dots", "check", "diamond"] as const;

/** Fill for a surface: a pattern (drawn over its shade) or a plain shade; "none" when neither is set. */
function paint(pid: string, shade?: string, pattern?: string, fallback = "none"): string {
  if (pattern && (PATTERNS as readonly string[]).includes(pattern)) return `url(#${pid}-${pattern}-${shade || "base"})`;
  return shade && SHADE[shade] ? SHADE[shade] : fallback;
}

/** Pattern definitions for every (pattern, shade) used in one picture, with ids local to that picture. */
function PatternDefs({ pid, items }: { pid: string; items: Json[] }) {
  const used = new Set<string>();
  for (const it of items) {
    if (it.pattern) used.add(`${it.pattern}|${it.shade || "base"}`);
    for (const w of it.wear ?? []) if (w.pattern) used.add(`${w.pattern}|${w.shade || "base"}`);
  }
  return (
    <defs>
      {[...used].map((k) => {
        const [pat, sh] = k.split("|");
        const base = SHADE[sh] ?? "#334155";
        const ink = sh === "dark" ? "#cbd5e1" : sh === "white" ? "#1f2a3d" : "#0b1324";
        const id = `${pid}-${pat}-${sh}`;
        return (
          <pattern key={id} id={id} width={5} height={5} patternUnits="userSpaceOnUse">
            <rect width={5} height={5} fill={base} />
            {pat === "stripes" && <rect width={5} height={2.2} fill={ink} />}
            {pat === "dots" && <circle cx={2.5} cy={2.5} r={1.5} fill={ink} />}
            {pat === "check" && <><rect width={2.5} height={2.5} fill={ink} /><rect x={2.5} y={2.5} width={2.5} height={2.5} fill={ink} /></>}
            {pat === "diamond" && <path d="M2.5,0.3 L4.7,2.5 L2.5,4.7 L0.3,2.5 z" fill={ink} />}
          </pattern>
        );
      })}
    </defs>
  );
}

function Wear({ w, x, y, s, d, pid }: { w: Json; x: number; y: number; s: number; d: number; pid: string }) {
  const f = paint(pid, w.shade, w.pattern, ACC);
  switch (w.kind) {
    case "hat": return <g data-testid="wear-hat"><path d={`M${x - 9 * s},${y - 26 * s} h${18 * s}`} stroke={INK} strokeWidth={2} /><path d={`M${x - 5 * s},${y - 26 * s} v${-6 * s} h${10 * s} v${6 * s}`} fill={f} stroke={INK} strokeWidth={1.5} /></g>;
    case "cap": return <g data-testid="wear-cap"><path d={`M${x - 6 * s},${y - 25 * s} a${6 * s},${5 * s} 0 0 1 ${12 * s},0 z`} fill={f} stroke={INK} strokeWidth={1.5} /><line x1={x + 4 * d * s} y1={y - 25 * s} x2={x + 11 * d * s} y2={y - 25 * s} stroke={INK} strokeWidth={2} /></g>;
    case "glasses": return <g data-testid="wear-glasses" fill="none" stroke={INK} strokeWidth={1.2}><circle cx={x - 2.5 * s} cy={y - 22 * s} r={2.2 * s} /><circle cx={x + 2.5 * s} cy={y - 22 * s} r={2.2 * s} /></g>;
    case "scarf": return <g data-testid="wear-scarf"><rect x={x - 7 * s} y={y - 18 * s} width={14 * s} height={5 * s} rx={1.5} fill={f} stroke={INK} strokeWidth={1} /><rect x={x + 1 * s} y={y - 14 * s} width={5.5 * s} height={13 * s} fill={f} stroke={INK} strokeWidth={1} /></g>;
    case "gloves": return <g data-testid="wear-gloves" fill={f} stroke={INK} strokeWidth={1}><circle cx={x - 8 * s} cy={y - 2 * s} r={3.4 * s} /><circle cx={x + 8 * s} cy={y - 2 * s} r={3.4 * s} /></g>;
    case "ribbon": return <g data-testid="wear-ribbon" fill={f} stroke={INK} strokeWidth={1}><path d={`M${x},${y - 28 * s} l${-5 * s},${-3 * s} v${6 * s} z M${x},${y - 28 * s} l${5 * s},${-3 * s} v${6 * s} z`} /></g>;
    case "backpack": return <rect data-testid="wear-backpack" x={x - (d > 0 ? 9 : -3) * s} y={y - 15 * s} width={6 * s} height={12 * s} rx={2} fill={f} stroke={INK} strokeWidth={1.2} />;
    default: return null;
  }
}

function Hair({ hair, x, y, s }: { hair: string; x: number; y: number; s: number }) {
  if (!hair) return null;
  const top = <path d={`M${x - 6 * s},${y - 22 * s} a${6 * s},${6 * s} 0 0 1 ${12 * s},0`} fill={INK} stroke={INK} strokeWidth={1} />;
  if (hair === "short") return <g data-testid="hair-short">{top}</g>;
  if (hair === "long") return <g data-testid="hair-long">{top}<path d={`M${x - 6 * s},${y - 22 * s} v${10 * s} M${x + 6 * s},${y - 22 * s} v${10 * s}`} stroke={INK} strokeWidth={2.5} /></g>;
  if (hair === "ponytail") return <g data-testid="hair-ponytail">{top}<path d={`M${x + 6 * s},${y - 24 * s} q${7 * s},${2 * s} ${5 * s},${10 * s}`} fill="none" stroke={INK} strokeWidth={2.5} /></g>;
  return <g data-testid="hair-braid">{top}<path d={`M${x - 6 * s},${y - 21 * s} l${-2 * s},${3 * s} l${2 * s},${3 * s} l${-2 * s},${3 * s} l${2 * s},${3 * s}`} fill="none" stroke={INK} strokeWidth={2} /></g>;
}

function Person({ it, x, y, s, seated, pid }: { it: Json; x: number; y: number; s: number; seated: boolean; pid: string }) {
  const pose: string = it.pose || "stand";
  const d = it.flip ? -1 : 1;
  const shirt = paint(pid, it.shade, it.pattern, "none");
  const head = <circle cx={x} cy={y - 22 * s} r={6 * s} fill={BG} stroke={INK} strokeWidth={2} />;
  const extras = (<>
    <Hair hair={it.hair || ""} x={x} y={y} s={s} />
    {(it.wear ?? []).map((w: Json, i: number) => <Wear key={i} w={w} x={x} y={y} s={s} d={d} pid={pid} />)}
  </>);
  if (pose === "sleep" || pose === "lie") {
    return (<g>
      <circle cx={x - 16 * s} cy={y + 6 * s} r={6 * s} fill={BG} stroke={INK} strokeWidth={2} />
      <line x1={x - 10 * s} y1={y + 6 * s} x2={x + 18 * s} y2={y + 6 * s} stroke={INK} strokeWidth={3} />
      {pose === "sleep" ? <text x={x - 8 * s} y={y - 8 * s} fontSize={11} fill={MUTE}>z z</text> : <circle cx={x - 18 * s} cy={y + 5 * s} r={1} fill={INK} />}
    </g>);
  }
  const sit = seated || pose === "sit" || pose === "play_game";
  const lean = pose === "pull" ? 7 * d * s : 0; // pulled forward along the lead
  const body = shirt !== "none"
    ? <rect x={x - 4 * s + lean / 2} y={y - 16 * s} width={8 * s} height={16 * s} rx={2} fill={shirt} stroke={INK} strokeWidth={1.5} data-testid="shirt" />
    : <line x1={x} y1={y - 16 * s} x2={x + lean} y2={y + 2 * s} stroke={INK} strokeWidth={3} />;
  const legs = sit
    ? <polyline points={`${x},${y + 2 * s} ${x + 12 * d * s},${y + 2 * s} ${x + 12 * d * s},${y + 16 * s}`} fill="none" stroke={INK} strokeWidth={3} />
    : pose === "walk" || pose === "run" || pose === "pull"
      ? <polyline points={`${x - 8 * s},${y + 20 * s} ${x + lean},${y + 2 * s} ${x + 9 * s + lean},${y + 20 * s}`} fill="none" stroke={INK} strokeWidth={3} />
      : <polyline points={`${x - 5 * s},${y + 20 * s} ${x},${y + 2 * s} ${x + 5 * s},${y + 20 * s}`} fill="none" stroke={INK} strokeWidth={3} />;
  let arms: ReactElement;
  if (pose === "raise_hand") arms = <polyline points={`${x - 8 * s},${y - 4 * s} ${x},${y - 12 * s} ${x + 8 * d * s},${y - 30 * s}`} fill="none" stroke={INK} strokeWidth={2.5} />;
  else if (pose === "wave") arms = <g><polyline points={`${x - 8 * s},${y - 2 * s} ${x},${y - 12 * s} ${x + 9 * d * s},${y - 24 * s}`} fill="none" stroke={INK} strokeWidth={2.5} /><path d={`M${x + 13 * d * s},${y - 30 * s} q${2 * d * s},${3 * s} 0 ${6 * s} M${x + 16 * d * s},${y - 31 * s} q${2 * d * s},${4 * s} 0 ${8 * s}`} fill="none" stroke={MUTE} strokeWidth={1} /></g>;
  else if (pose === "read") // an open book held in front: reading is visible, not only an arm angle
    arms = <g><polyline points={`${x},${y - 12 * s} ${x + 12 * d * s},${y - 6 * s}`} fill="none" stroke={INK} strokeWidth={2.5} />
      <path d={`M${x + 10 * d * s},${y - 12 * s} l${6 * d * s},${-3 * s} l${6 * d * s},${3 * s} v${8 * s} l${-6 * d * s},${-3 * s} l${-6 * d * s},${3 * s} z`} fill={ACC} stroke={INK} strokeWidth={1} data-testid="held-book" /></g>;
  else if (pose === "hold") arms = <polyline points={`${x},${y - 12 * s} ${x + 12 * d * s},${y - 6 * s}`} fill="none" stroke={INK} strokeWidth={2.5} />;
  else if (pose === "call") // a phone at the ear
    arms = <g><polyline points={`${x},${y - 12 * s} ${x + 6 * d * s},${y - 18 * s} ${x + 4 * d * s},${y - 22 * s}`} fill="none" stroke={INK} strokeWidth={2.5} />
      <rect x={x + (d > 0 ? 3 : -8) * s} y={y - 29 * s} width={5 * s} height={10 * s} rx={1.5} fill={ACC} stroke={INK} strokeWidth={1} data-testid="held-phone" /></g>;
  else if (pose === "scared") // hands at the chest, an exclamation mark
    arms = <g><polyline points={`${x - 6 * s},${y - 4 * s} ${x - 2 * s},${y - 12 * s} ${x + 2 * s},${y - 4 * s}`} fill="none" stroke={INK} strokeWidth={2.5} /><text x={x + 9 * d * s} y={y - 26 * s} fontSize={12} fontWeight={700} fill={ACC} data-testid="scared">!</text></g>;
  else if (pose === "play_game") // a game controller held in both hands
    arms = <g><polyline points={`${x - 2 * s},${y - 12 * s} ${x + 9 * d * s},${y - 7 * s}`} fill="none" stroke={INK} strokeWidth={2.5} /><rect x={x + 7 * d * s - 4 * s} y={y - 10 * s} width={8 * s} height={4 * s} rx={2} fill={ACC} data-testid="controller" /></g>;
  else if (pose === "pull") // arm stretched forward holding a lead
    arms = <polyline points={`${x + lean / 2},${y - 12 * s} ${x + 16 * d * s},${y - 6 * s}`} fill="none" stroke={INK} strokeWidth={2.5} />;
  else arms = <polyline points={`${x - 8 * s},${y - 2 * s} ${x},${y - 12 * s} ${x + 8 * s},${y - 2 * s}`} fill="none" stroke={INK} strokeWidth={2.5} />;
  return <g>{head}{body}{arms}{legs}{extras}</g>;
}

/** One drawn item at (x, y), scale s. `body` is the fill of its main surface (shade / pattern). */
function Thing({ it, x, y, s, pid }: { it: Json; x: number; y: number; s: number; pid: string }): ReactNode {
  const flip = !!it.flip;
  const body = paint(pid, it.shade, it.pattern);
  const round = it.shape === "round";
  const on = it.state === "on";
  switch (it.kind) {
    case "chair": return <g stroke={MUTE} strokeWidth={2.5} fill="none"><line x1={x - 12 * s} y1={y} x2={x + 12 * s} y2={y} /><line x1={x - 10 * s} y1={y} x2={x - 10 * s} y2={y + 12 * s} /><line x1={x + 10 * s} y1={y} x2={x + 10 * s} y2={y + 12 * s} /></g>;
    case "table": return <g stroke={MUTE} strokeWidth={2.5}><line x1={x - 20 * s} y1={y} x2={x + 20 * s} y2={y} /><line x1={x - 16 * s} y1={y} x2={x - 16 * s} y2={y + 16 * s} /><line x1={x + 16 * s} y1={y} x2={x + 16 * s} y2={y + 16 * s} /></g>;
    case "sofa": return <rect x={x - 22 * s} y={y - 8 * s} width={44 * s} height={16 * s} rx={5} fill={body} stroke={MUTE} strokeWidth={2.5} />;
    case "bed": return <rect x={x - 26 * s} y={y - 6 * s} width={52 * s} height={12 * s} rx={3} fill={body} stroke={MUTE} strokeWidth={2.5} />;
    case "shelf": return <g stroke={MUTE} strokeWidth={2.5} fill="none"><rect x={x - 14 * s} y={y - 20 * s} width={28 * s} height={40 * s} /><line x1={x - 14 * s} y1={y} x2={x + 14 * s} y2={y} /></g>;
    case "window": return <g stroke={MUTE} strokeWidth={2} fill="none"><rect x={x - 14 * s} y={y - 12 * s} width={28 * s} height={24 * s} /><line x1={x} y1={y - 12 * s} x2={x} y2={y + 12 * s} /><line x1={x - 14 * s} y1={y} x2={x + 14 * s} y2={y} /></g>;
    case "door": return <g stroke={MUTE} strokeWidth={2} fill={body}><rect x={x - 10 * s} y={y - 22 * s} width={20 * s} height={44 * s} /><circle cx={x + 6 * s} cy={y} r={1.8} fill={MUTE} /></g>;
    case "clock": return <g>{it.shape === "square" ? <rect x={x - 20 * s} y={y - 20 * s} width={40 * s} height={40 * s} rx={4} fill={body} stroke={ACC} strokeWidth={2.5} /> : <circle cx={x} cy={y} r={20 * s} fill={body} stroke={ACC} strokeWidth={2.5} />}<text x={x} y={y + 5} fontSize={15} fontWeight={700} textAnchor="middle" fill={it.shade === "white" ? BG : INK}>{String(it.label || "")}</text></g>;
    case "watch": { // strap + face: shape (round / square) x shade of face and strap (2025 1B)
      const face = paint(pid, it.shade, it.pattern, BG);
      return <g data-testid="watch"><rect x={x - 4 * s} y={y - 22 * s} width={8 * s} height={44 * s} fill="none" stroke={INK} strokeWidth={1.5} />
        {round ? <circle cx={x} cy={y} r={11 * s} fill={face} stroke={INK} strokeWidth={2} /> : <rect x={x - 11 * s} y={y - 11 * s} width={22 * s} height={22 * s} rx={3} fill={face} stroke={INK} strokeWidth={2} />}
        {it.label && <text x={x} y={y + 3.5} fontSize={9} fontWeight={700} textAnchor="middle" fill={it.shade === "white" ? BG : INK}>{String(it.label)}</text>}</g>;
    }
    case "sign": return <g><rect x={x - 28 * s} y={y - 8 * s} width={56 * s} height={16 * s} rx={round ? 8 : 3} fill={body} stroke={MUTE} strokeWidth={1.5} /><text x={x} y={y + 4} fontSize={11} textAnchor="middle" fill={it.shade === "white" ? BG : INK}>{String(it.label || "")}</text></g>;
    case "box": case "cooler": {
      const open = it.state === "open" || it.state === "full" || it.state === "empty";
      const w = it.kind === "cooler" ? 26 : 18, h = it.kind === "cooler" ? 16 : 18;
      return <g><rect x={x - (w / 2) * s} y={y - (h / 2) * s} width={w * s} height={h * s} rx={round ? 6 : 1} fill={body} stroke={INK} strokeWidth={2} />
        {open ? <path d={`M${x - (w / 2) * s},${y - (h / 2) * s} l${-4 * s},${-10 * s} h${w * s} l${4 * s},${10 * s}`} fill="none" stroke={INK} strokeWidth={1.5} /> : <line x1={x - (w / 2 + 1) * s} y1={y - (h / 2) * s} x2={x + (w / 2 + 1) * s} y2={y - (h / 2) * s} stroke={INK} strokeWidth={3} />}
        {it.state === "full" && [0, 1, 2, 3, 4].map((k) => <ellipse key={k} cx={x - 9 * s + k * 4.5 * s} cy={y - (h / 2 - 3) * s} rx={3 * s} ry={1.5 * s} fill={ACC} />)}</g>;
    }
    case "bag": return <g stroke={INK} strokeWidth={2} fill={body}><rect x={x - 8 * s} y={y - 5 * s} width={16 * s} height={13 * s} rx={2} /><path d={`M${x - 4 * s},${y - 5 * s} q${4 * s},${-7 * s} ${8 * s},0`} fill="none" /></g>;
    case "book": return <rect x={x - 6 * s} y={y - 4 * s} width={12 * s} height={8 * s} fill={paint(pid, it.shade, it.pattern, ACC)} stroke={INK} strokeWidth={1} />;
    case "phone": // a smartphone; state on = lit screen
      return <rect x={x - 4 * s} y={y - 7 * s} width={8 * s} height={14 * s} rx={1.5} fill={it.state === "on" ? ACC : paint(pid, it.shade, it.pattern, BG)} fillOpacity={it.state === "on" ? 0.6 : 1} stroke={INK} strokeWidth={1.6} />;
    case "cup": return <path d={`M${x - 5 * s},${y - 5 * s} h${10 * s} l${-2 * s},${10 * s} h${-6 * s} z`} fill={body} stroke={INK} strokeWidth={1.8} />;
    case "bottle": return <g><path d={`M${x - 3 * s},${y - 16 * s} h${6 * s} v${5 * s} l${4 * s},${4 * s} v${19 * s} h${-14 * s} v${-19 * s} l${4 * s},${-4 * s} z`} fill={body} stroke={INK} strokeWidth={1.8} /></g>;
    case "umbrella": // open above shoulder height, closed when lying low
      return it.y < 60
        ? <g stroke={ACC} strokeWidth={2.5} fill={body}><path d={`M${x - 18 * s},${y} a${18 * s},${12 * s} 0 0 1 ${36 * s},0 z`} /><line x1={x} y1={y} x2={x} y2={y + 18 * s} /></g>
        : <g stroke={ACC} strokeWidth={2.5} fill="none"><line x1={x - 14 * s} y1={y + 2 * s} x2={x + 14 * s} y2={y - 2 * s} /><path d={`M${x + 14 * s},${y - 2 * s} q${4 * s},${3 * s} 0 ${6 * s}`} /></g>;
    case "tree": { // state: bare branches / leaves (default) / with fruit
      const trunk = <line x1={x} y1={y + 2 * s} x2={x} y2={y + 22 * s} stroke={MUTE} strokeWidth={3} />;
      if (it.state === "bare") return <g data-testid="tree-bare">{trunk}<path d={`M${x},${y + 4 * s} l${-10 * s},${-14 * s} M${x},${y + 4 * s} l${10 * s},${-14 * s} M${x},${y} v${-16 * s} M${x - 6 * s},${y - 6 * s} l${-6 * s},${-2 * s} M${x + 6 * s},${y - 6 * s} l${6 * s},${-2 * s}`} stroke={MUTE} strokeWidth={1.8} fill="none" /></g>;
      return <g>{trunk}<circle cx={x} cy={y - 10 * s} r={12 * s} fill={MUTE} fillOpacity={0.45} stroke={MUTE} strokeWidth={2} data-testid="tree-leaves" />
        {it.state === "fruit" && [[-5, -14], [4, -8], [6, -16], [-3, -5]].map(([dx, dy], k) => <circle key={k} cx={x + dx * s} cy={y + dy * s} r={2 * s} fill={ACC} data-testid="fruit" />)}</g>;
    }
    case "cat": case "dog": {
      const sleep = it.pose === "sleep" || it.pose === "lie";
      const fur = paint(pid, it.shade, it.pattern);
      const tail = it.state === "full" ? 12 : 6; // 'full' = a long tail (2024: a cat's tail), default short
      return <g stroke={INK} strokeWidth={2} fill={fur}><ellipse cx={x} cy={y} rx={12 * s} ry={6 * s} /><circle cx={x + (flip ? -13 : 13) * s} cy={y - 4 * s} r={5 * s} />
        <path d={`M${x + (flip ? 12 : -12) * s},${y - 2 * s} q${(flip ? 4 : -4) * s},${-tail * s} ${(flip ? 2 : -2) * s},${-tail * s}`} fill="none" />
        {it.kind === "cat" && <path d={`M${x + (flip ? -16 : 10) * s},${y - 8 * s} l${2 * s},${-5 * s} l${2 * s},${5 * s}`} />}
        {sleep && <text x={x + 4} y={y - 12 * s} fontSize={10} fill={MUTE} stroke="none">z z</text>}
        {it.pose === "run" && <path d={`M${x - 20 * s},${y - 3 * s} h${-6 * s} M${x - 20 * s},${y + 2 * s} h${-6 * s}`} stroke={MUTE} strokeWidth={1} />}</g>;
    }
    case "car": case "bus": case "train": {
      const w = it.kind === "car" ? 34 : 48;
      return <g stroke={INK} strokeWidth={2} fill={body}><rect x={x - (w / 2) * s} y={y - 9 * s} width={w * s} height={14 * s} rx={3} /><circle cx={x - (w / 3) * s} cy={y + 7 * s} r={3.5 * s} fill="none" /><circle cx={x + (w / 3) * s} cy={y + 7 * s} r={3.5 * s} fill="none" />{it.label && <text x={x} y={y + 1} fontSize={9} textAnchor="middle" fill={INK} stroke="none">{String(it.label)}</text>}</g>;
    }
    case "bike": return <g stroke={INK} strokeWidth={2} fill="none"><circle cx={x - 10 * s} cy={y + 4 * s} r={7 * s} /><circle cx={x + 10 * s} cy={y + 4 * s} r={7 * s} /><polyline points={`${x - 10 * s},${y + 4 * s} ${x - 2 * s},${y - 6 * s} ${x + 10 * s},${y + 4 * s}`} /></g>;
    case "fan": // handheld fan: blade disc + handle; shade / pattern on the handle, shape round (thin) / square (thick) handle (2024)
      return <g data-testid="fan"><circle cx={x} cy={y - 10 * s} r={10 * s} fill={BG} stroke={INK} strokeWidth={1.8} /><circle cx={x} cy={y - 10 * s} r={3 * s} fill={MUTE} />
        {[0, 45, 90, 135].map((a) => <line key={a} x1={x - 9 * s * Math.cos((a * Math.PI) / 180)} y1={y - 10 * s - 9 * s * Math.sin((a * Math.PI) / 180)} x2={x + 9 * s * Math.cos((a * Math.PI) / 180)} y2={y - 10 * s + 9 * s * Math.sin((a * Math.PI) / 180)} stroke={MUTE} strokeWidth={0.8} />)}
        <rect x={x - (round ? 2.5 : 5) * s} y={y} width={(round ? 5 : 10) * s} height={18 * s} rx={2} fill={paint(pid, it.shade, it.pattern, BG)} stroke={INK} strokeWidth={1.5} /></g>;
    case "tv": case "laptop": { // screen on (lit, with a picture) / off (dark)
      const scr = on ? ACC : "#111827";
      return it.kind === "tv"
        ? <g data-testid={`tv-${on ? "on" : "off"}`}><rect x={x - 22 * s} y={y - 15 * s} width={44 * s} height={28 * s} rx={2} fill={scr} fillOpacity={on ? 0.45 : 1} stroke={INK} strokeWidth={2} />{on && <path d={`M${x - 14 * s},${y + 6 * s} l${8 * s},${-12 * s} l${6 * s},${8 * s} l${5 * s},${-5 * s} l${8 * s},${9 * s}`} fill="none" stroke={INK} strokeWidth={1.2} />}<line x1={x} y1={y + 13 * s} x2={x} y2={y + 18 * s} stroke={INK} strokeWidth={2} /><line x1={x - 10 * s} y1={y + 18 * s} x2={x + 10 * s} y2={y + 18 * s} stroke={INK} strokeWidth={2} /></g>
        : <g data-testid={`laptop-${on ? "on" : "off"}`}><rect x={x - 13 * s} y={y - 16 * s} width={26 * s} height={17 * s} rx={1.5} fill={scr} fillOpacity={on ? 0.45 : 1} stroke={INK} strokeWidth={1.8} /><path d={`M${x - 16 * s},${y + 1 * s} h${32 * s} l${-3 * s},${4 * s} h${-26 * s} z`} fill="none" stroke={INK} strokeWidth={1.8} /></g>;
    }
    case "lamp": return <g><path d={`M${x - 8 * s},${y - 10 * s} h${16 * s} l${-4 * s},${-9 * s} h${-8 * s} z`} fill={body} stroke={INK} strokeWidth={1.8} /><line x1={x} y1={y - 10 * s} x2={x} y2={y + 12 * s} stroke={INK} strokeWidth={2} /><line x1={x - 7 * s} y1={y + 12 * s} x2={x + 7 * s} y2={y + 12 * s} stroke={INK} strokeWidth={2} />{on && <path d={`M${x - 10 * s},${y - 6 * s} l${-4 * s},${4 * s} M${x},${y - 6 * s} v${6 * s} M${x + 10 * s},${y - 6 * s} l${4 * s},${4 * s}`} stroke={ACC} strokeWidth={1.5} data-testid="lamp-on" />}</g>;
    case "envelope": return <g><rect x={x - 9 * s} y={y - 6 * s} width={18 * s} height={12 * s} fill={paint(pid, it.shade, it.pattern, BG)} stroke={INK} strokeWidth={1.5} /><path d={`M${x - 9 * s},${y - 6 * s} l${9 * s},${7 * s} l${9 * s},${-7 * s}`} fill="none" stroke={INK} strokeWidth={1.2} /></g>;
    case "ball": return <circle cx={x} cy={y} r={5 * s} fill={paint(pid, it.shade, it.pattern, ACC)} stroke={INK} strokeWidth={1.5} />;
    case "bear": return <g fill={paint(pid, it.shade, it.pattern, MUTE)} stroke={INK} strokeWidth={1.2}><circle cx={x - 4 * s} cy={y - 11 * s} r={2.2 * s} /><circle cx={x + 4 * s} cy={y - 11 * s} r={2.2 * s} /><circle cx={x} cy={y - 7 * s} r={5 * s} /><ellipse cx={x} cy={y + 3 * s} rx={6 * s} ry={7 * s} /></g>;
    case "fish": return <g fill={paint(pid, it.shade, it.pattern, ACC)} stroke={INK} strokeWidth={1}><ellipse cx={x} cy={y} rx={7 * s} ry={3 * s} /><path d={`M${x + (flip ? -7 : 7) * s},${y} l${(flip ? -4 : 4) * s},${-3 * s} v${6 * s} z`} /></g>;
    case "apple": return <g><circle cx={x} cy={y} r={4.5 * s} fill={paint(pid, it.shade, it.pattern, ACC)} stroke={INK} strokeWidth={1.2} /><line x1={x} y1={y - 4.5 * s} x2={x + 1.5 * s} y2={y - 7 * s} stroke={INK} strokeWidth={1.2} /></g>;
    case "leaf": return <path d={`M${x - 5 * s},${y} q${5 * s},${-6 * s} ${10 * s},0 q${-5 * s},${6 * s} ${-10 * s},0 z`} fill={paint(pid, it.shade, it.pattern, MUTE)} stroke={INK} strokeWidth={1} />;
    case "flower": return <g><line x1={x} y1={y} x2={x} y2={y + 14 * s} stroke={MUTE} strokeWidth={1.8} />{[0, 72, 144, 216, 288].map((a) => <circle key={a} cx={x + 3.5 * s * Math.cos((a * Math.PI) / 180)} cy={y + 3.5 * s * Math.sin((a * Math.PI) / 180)} r={2.4 * s} fill={paint(pid, it.shade, it.pattern, ACC)} />)}<circle cx={x} cy={y} r={1.6 * s} fill={INK} /></g>;
    case "snowman": return <g fill={SHADE.white} stroke={INK} strokeWidth={1.2} data-testid="snowman"><circle cx={x} cy={y + 4 * s} r={8 * s} /><circle cx={x} cy={y - 8 * s} r={5.5 * s} /></g>;
    case "cushion": return <ellipse cx={x} cy={y} rx={16 * s} ry={10 * s} fill={paint(pid, it.shade, it.pattern, MUTE)} stroke={INK} strokeWidth={1.5} />;
    case "bowl": return <path d={`M${x - 9 * s},${y - 3 * s} h${18 * s} a${9 * s},${6 * s} 0 0 1 ${-18 * s},0 z`} fill={body} stroke={INK} strokeWidth={1.5} />;
    case "plate": return <ellipse cx={x} cy={y} rx={10 * s} ry={3 * s} fill={body} stroke={INK} strokeWidth={1.5} />;
    case "cake": return <g><rect x={x - 9 * s} y={y - 5 * s} width={18 * s} height={10 * s} rx={2} fill={paint(pid, it.shade, it.pattern, SHADE.white)} stroke={INK} strokeWidth={1.5} /><line x1={x} y1={y - 5 * s} x2={x} y2={y - 10 * s} stroke={ACC} strokeWidth={1.5} /></g>;
    case "sun": return <g stroke={ACC} strokeWidth={1.5}><circle cx={x} cy={y} r={6 * s} fill={ACC} />{[0, 45, 90, 135, 180, 225, 270, 315].map((a) => <line key={a} x1={x + 8 * s * Math.cos((a * Math.PI) / 180)} y1={y + 8 * s * Math.sin((a * Math.PI) / 180)} x2={x + 11 * s * Math.cos((a * Math.PI) / 180)} y2={y + 11 * s * Math.sin((a * Math.PI) / 180)} />)}</g>;
    case "cloud": return <path d={`M${x - 12 * s},${y + 4 * s} a${5 * s},${5 * s} 0 0 1 ${3 * s},${-9 * s} a${7 * s},${7 * s} 0 0 1 ${13 * s},${-1 * s} a${5 * s},${5 * s} 0 0 1 ${8 * s},${10 * s} z`} fill={paint(pid, it.shade, it.pattern, MUTE)} stroke={INK} strokeWidth={1.2} />;
    case "rain": return <g stroke={ACC} strokeWidth={1.4}>{[-8, -2, 4, 10].map((dx, k) => <line key={k} x1={x + dx * s} y1={y - 6 * s + (k % 2) * 4 * s} x2={x + (dx - 2) * s} y2={y + (k % 2) * 4 * s} />)}</g>;
    case "snow": return <g fill={SHADE.white}>{[[-8, -4], [0, 2], [8, -6], [-4, 8], [6, 6]].map(([dx, dy], k) => <circle key={k} cx={x + dx * s} cy={y + dy * s} r={1.3 * s} />)}</g>;
    case "building": return <g><rect x={x - 12 * s} y={y - 22 * s} width={24 * s} height={44 * s} fill={body} stroke={INK} strokeWidth={1.8} />{[0, 1, 2].map((r) => [0, 1].map((c) => <rect key={`${r}${c}`} x={x - 8 * s + c * 10 * s} y={y - 18 * s + r * 11 * s} width={6 * s} height={6 * s} fill="none" stroke={MUTE} strokeWidth={1} />))}{it.label && <text x={x} y={y + 18 * s} fontSize={8} textAnchor="middle" fill={INK}>{String(it.label)}</text>}</g>;
    case "bench": return <g stroke={MUTE} strokeWidth={2.5}><line x1={x - 18 * s} y1={y} x2={x + 18 * s} y2={y} /><line x1={x - 18 * s} y1={y - 7 * s} x2={x + 18 * s} y2={y - 7 * s} /><line x1={x - 14 * s} y1={y} x2={x - 14 * s} y2={y + 9 * s} /><line x1={x + 14 * s} y1={y} x2={x + 14 * s} y2={y + 9 * s} /></g>;
    case "ticket": return <g><rect x={x - 10 * s} y={y - 5 * s} width={20 * s} height={10 * s} rx={1.5} fill={paint(pid, it.shade, it.pattern, BG)} stroke={ACC} strokeWidth={1.5} />{it.label && <text x={x} y={y + 3} fontSize={7} textAnchor="middle" fill={INK}>{String(it.label)}</text>}</g>;
    case "guitar": return <g stroke={INK} strokeWidth={1.5} fill={paint(pid, it.shade, it.pattern, MUTE)}><circle cx={x} cy={y + 5 * s} r={6 * s} /><circle cx={x} cy={y - 3 * s} r={4.5 * s} /><line x1={x} y1={y - 7 * s} x2={x} y2={y - 20 * s} strokeWidth={2.5} /></g>;
    case "hat": return <g fill={body} stroke={INK} strokeWidth={1.5}><path d={`M${x - 11 * s},${y + 3 * s} h${22 * s}`} strokeWidth={2.5} /><path d={`M${x - 6 * s},${y + 3 * s} v${-8 * s} h${12 * s} v${8 * s}`} /></g>;
    case "shirt": return <path d={`M${x - 5 * s},${y - 9 * s} l${-7 * s},${5 * s} l${3 * s},${4 * s} l${3 * s},${-2 * s} v${11 * s} h${12 * s} v${-11 * s} l${3 * s},${2 * s} l${3 * s},${-4 * s} l${-7 * s},${-5 * s} q${-5 * s},${4 * s} ${-10 * s},0 z`} fill={paint(pid, it.shade, it.pattern, MUTE)} stroke={INK} strokeWidth={1.2} />;
    case "shoe": return <path d={`M${x - 8 * s},${y + 3 * s} v${-6 * s} h${6 * s} l${3 * s},${3 * s} h${6 * s} q${3 * s},0 ${3 * s},${3 * s} z`} fill={paint(pid, it.shade, it.pattern, MUTE)} stroke={INK} strokeWidth={1.2} />;
    default: return null;
  }
}

function Item({ it, seated, pid }: { it: Json; seated: boolean; pid: string }) {
  const x = (it.x / 100) * W, y = (it.y / 100) * H, s = (it.size ?? 100) / 100;
  const n: number = Math.max(1, Math.min(12, it.count ?? 1));
  // count: copies in a compact grid around the item's place (fish in a cooler, apples, envelopes)
  const step = (it.kind === "fish" ? 20 : 16) * s, cols = Math.min(n, 4), rows = Math.ceil(n / 4);
  const at = (k: number) => [x + ((k % 4) - (cols - 1) / 2) * step, y + (Math.floor(k / 4) - (rows - 1) / 2) * step * 0.8];
  const one = (cx: number, cy: number, k: number) =>
    it.kind === "person" ? <Person key={k} it={it} x={cx} y={cy} s={s} seated={seated} pid={pid} /> : <g key={k}>{Thing({ it, x: cx, y: cy, s, pid })}</g>;
  return (
    <g data-kind={it.kind} data-count={n}>
      {[...Array(n)].map((_, k) => { const [cx, cy] = n === 1 ? [x, y] : at(k); return one(cx, cy, k); })}
      {it.mark ? <g data-testid="scene-mark"><circle cx={x + 12 * s} cy={y - 18 * s} r={7} fill={BG} stroke={ACC} strokeWidth={1.5} /><text x={x + 12 * s} y={y - 14.5 * s} fontSize={9} fontWeight={700} textAnchor="middle" fill={ACC}>{it.mark}</text></g> : null}
    </g>
  );
}

function Panel({ items, pid, number, label }: { items: Json[]; pid: string; number?: number; label: string }) {
  const seats = items.filter((it) => it.kind === "chair" || it.kind === "sofa" || it.kind === "bed" || it.kind === "bench" || it.kind === "cushion");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} data-testid="picture-panel">
      <PatternDefs pid={pid} items={items} />
      <rect x={1} y={1} width={W - 2} height={H - 2} rx={10} fill="rgba(255,255,255,.03)" stroke="rgba(148,163,184,.35)" />
      {items.map((it, j) => (
        <Item key={j} it={it} pid={pid} seated={it.kind === "person" && seats.some((c) => Math.abs(c.x - it.x) < 15 && c.y > it.y)} />
      ))}
      {number != null && (<>
        <circle cx={16} cy={16} r={11} fill={BG} stroke={ACC} />
        <text x={16} y={21} fontSize={14} fontWeight={700} textAnchor="middle" fill={ACC}>{number}</text>
      </>)}
    </svg>
  );
}

export function PictureSet({ fig, highlight = [] }: { fig: Json; highlight?: string[] }) {
  const pics: Json[] = fig.pictures ?? [];
  const base = `pic-${String(fig.id || "f").replace(/[^a-zA-Z0-9_-]/g, "")}`;
  if (fig.kind === "labeled_scene") {
    const p = pics[0];
    if (!p) return null;
    return (
      <div className="picture-set scene" role="group" aria-label={fig.title || "絵"} data-testid="picture-set">
        <figure className={`pic${highlight.includes(p.id) ? " hl" : ""}`}>
          <Panel items={p.items as Json[]} pid={`${base}-${p.id}`} label="絵（番号1〜4の付いたもの）" />
        </figure>
      </div>
    );
  }
  return (
    <div className="picture-set" role="group" aria-label={fig.title || "絵"} data-testid="picture-set">
      {pics.map((p, i) => (
        <figure key={p.id} className={`pic${highlight.includes(p.id) ? " hl" : ""}`}>
          <Panel items={p.items as Json[]} pid={`${base}-${p.id}`} number={i + 1} label={`絵 ${i + 1}`} />
        </figure>
      ))}
    </div>
  );
}
