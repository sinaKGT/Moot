// Geometric avatars, generated from a name: the same name always gets the same shape.

const SHAPES = ["hexagon", "diamond", "triangle", "ring", "square", "pentagon"] as const;
type Shape = (typeof SHAPES)[number];

export function shapeFor(name: string): Shape {
  let hash = 7;
  for (const char of name.toLowerCase()) hash = (hash * 31 + char.codePointAt(0)!) >>> 0;
  return SHAPES[hash % SHAPES.length];
}

function outline(shape: Shape, color: string) {
  const line = { fill: "none", stroke: color, strokeWidth: 2 };
  switch (shape) {
    case "hexagon":
      return <polygon points="13,2 23,7.5 23,18.5 13,24 3,18.5 3,7.5" {...line} />;
    case "diamond":
      return <polygon points="13,2.5 23.5,13 13,23.5 2.5,13" {...line} />;
    case "triangle":
      return <polygon points="13,3 24,22 2,22" {...line} />;
    case "ring":
      return <circle cx="13" cy="13" r="10" {...line} strokeDasharray="3 3" />;
    case "square":
      return <rect x="4" y="4" width="18" height="18" rx="3" {...line} />;
    case "pentagon":
      return <polygon points="13,2.5 23.5,10 19.5,23 6.5,23 2.5,10" {...line} />;
  }
}

export function Avatar({ name, color, size = 26 }: { name: string; color: string; size?: number }) {
  const shape = shapeFor(name);
  return (
    <svg width={size} height={size} viewBox="0 0 26 26" aria-hidden="true">
      {outline(shape, color)}
      <circle cx="13" cy={shape === "triangle" ? 15.5 : 13} r="3.6" fill={color} />
    </svg>
  );
}

export function ModeratorMark({ size = 26, color = "#e2e8f0" }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 26 26" aria-hidden="true">
      <circle cx="13" cy="13" r="10" fill="none" stroke={color} strokeWidth="2" />
      <circle cx="13" cy="13" r="4.5" fill="none" stroke={color} strokeWidth="2" />
    </svg>
  );
}
