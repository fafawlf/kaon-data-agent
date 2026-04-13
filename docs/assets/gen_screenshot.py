"""Generate an SVG terminal screenshot from demo_output.txt."""
import html
import re
import os

# ANSI color map -> SVG colors
ANSI_COLORS = {
    "30": "#585b70",  # black
    "31": "#f38ba8",  # red
    "32": "#a6e3a1",  # green
    "33": "#f9e2af",  # yellow
    "34": "#89b4fa",  # blue
    "35": "#cba6f7",  # magenta
    "36": "#94e2d5",  # cyan
    "37": "#cdd6f4",  # white
}

BG_COLOR = "#1e1e2e"
FG_COLOR = "#cdd6f4"
FONT = "JetBrains Mono, Menlo, Monaco, Consolas, monospace"
FONT_SIZE = 13
LINE_HEIGHT = 20
PADDING_X = 24
PADDING_Y = 16
TITLE_BAR_H = 36
MAX_LINES = 50  # truncate for readability


def ansi_to_svg_spans(text: str) -> str:
    """Convert ANSI escape sequences to SVG tspan elements."""
    result = []
    current_color = None
    current_bold = False

    # Remove ANSI and rebuild with spans
    parts = re.split(r'\x1b\[([0-9;]+)m', text)

    for i, part in enumerate(parts):
        if i % 2 == 0:
            # Text content
            if not part:
                continue
            escaped = html.escape(part)
            attrs = []
            if current_color:
                attrs.append(f'fill="{current_color}"')
            if current_bold:
                attrs.append('font-weight="bold"')
            if attrs:
                result.append(f'<tspan {" ".join(attrs)}>{escaped}</tspan>')
            else:
                result.append(escaped)
        else:
            # ANSI code
            codes = part.split(";")
            for code in codes:
                if code == "0":
                    current_color = None
                    current_bold = False
                elif code == "1":
                    current_bold = True
                elif code == "2":
                    current_color = "#6c7086"  # dim
                elif code in ANSI_COLORS:
                    current_color = ANSI_COLORS[code]

    return "".join(result)


def generate_svg(input_path: str, output_path: str):
    with open(input_path, "r") as f:
        lines = f.readlines()

    # Truncate
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES]
        lines.append("  ...\n")

    # Calculate dimensions
    max_chars = max((len(re.sub(r'\x1b\[[0-9;]+m', '', l.rstrip())) for l in lines), default=80)
    width = max(800, PADDING_X * 2 + max_chars * (FONT_SIZE * 0.6))
    height = TITLE_BAR_H + PADDING_Y * 2 + len(lines) * LINE_HEIGHT + 10

    svg_lines = []
    svg_lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}">')
    svg_lines.append(f'  <rect width="100%" height="100%" rx="10" fill="{BG_COLOR}" />')

    # Title bar with traffic lights
    svg_lines.append(f'  <circle cx="20" cy="18" r="6" fill="#f38ba8" />')
    svg_lines.append(f'  <circle cx="38" cy="18" r="6" fill="#f9e2af" />')
    svg_lines.append(f'  <circle cx="56" cy="18" r="6" fill="#a6e3a1" />')
    svg_lines.append(f'  <text x="{width/2:.0f}" y="22" fill="#6c7086" font-family="{FONT}" font-size="12" text-anchor="middle">l3-agent demo</text>')

    # Content
    y = TITLE_BAR_H + PADDING_Y + FONT_SIZE
    for line in lines:
        line = line.rstrip()
        if not line:
            y += LINE_HEIGHT
            continue
        content = ansi_to_svg_spans(line)
        svg_lines.append(f'  <text x="{PADDING_X}" y="{y:.0f}" fill="{FG_COLOR}" font-family="{FONT}" font-size="{FONT_SIZE}" xml:space="preserve">{content}</text>')
        y += LINE_HEIGHT

    svg_lines.append('</svg>')

    with open(output_path, "w") as f:
        f.write("\n".join(svg_lines))

    print(f"Generated {output_path} ({width:.0f}x{height:.0f})")


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_path = os.path.join(script_dir, "demo_output.txt")
    output_path = os.path.join(script_dir, "demo.svg")
    generate_svg(input_path, output_path)
