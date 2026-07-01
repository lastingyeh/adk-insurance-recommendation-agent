import re
from pathlib import Path

# Find repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
MAKEFILE_PATH = REPO_ROOT / "Makefile"
INDEX_HTML_PATH = REPO_ROOT / "docs" / "index.html"

# 1. Parse Makefile
makefile_content = MAKEFILE_PATH.read_text(encoding="utf-8")
makefile_lines = makefile_content.splitlines()

target_map = {}
for idx, line in enumerate(makefile_lines):
    match = re.match(r"^([a-zA-Z0-9_-]+):", line)
    if match:
        target = match.group(1)
        # 1-based index
        target_map[target] = idx + 1

print(f"Parsed {len(target_map)} targets from Makefile.")

# 2. Parse docs/index.html
html_content = INDEX_HTML_PATH.read_text(encoding="utf-8")

# 3. Update MAKE_LINKS block
# Example: "test-api":"Makefile#L272"
def replace_make_links(match):
    target = match.group(1)
    if target in target_map:
        return f'"{target}":"Makefile#L{target_map[target]}"'
    return match.group(0)

html_content = re.sub(r'"([a-zA-Z0-9_-]+)"\s*:\s*"Makefile#L\d+"', replace_make_links, html_content)

# 4. Update inline Makefile links
# Example: href="blob/main/Makefile#L165" target="_blank" rel="noopener"><code>make db-reset</code>
def replace_inline_links(match):
    extra_attrs = match.group(1)
    target = match.group(2)
    if target in target_map:
        return f'href="blob/main/Makefile#L{target_map[target]}"{extra_attrs}><code>make {target}</code>'
    return match.group(0)

pattern = r'href="blob/main/Makefile#L\d+"([^>]*)>\s*<code>make ([a-zA-Z0-9_-]+)</code>'
html_content = re.sub(pattern, replace_inline_links, html_content)

# Write back to docs/index.html
INDEX_HTML_PATH.write_text(html_content, encoding="utf-8")
print("Successfully updated docs/index.html line numbers!")
