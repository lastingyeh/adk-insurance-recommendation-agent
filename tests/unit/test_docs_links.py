"""docs/index.html 的 GitHub 連結健全性測試（純檔案系統，不需網路）。

簡報頁面的連結改為相對路徑（blob/main、tree/main），載入時由單一
``GH_REPO`` 常數補上網域。本測試確保：

1. 每個 blob/tree 連結指向 repo 內真實存在的檔案 / 目錄（避免 404）。
2. 每個 ``Makefile#L<行號>`` 連結，該行確實是對應 make 指令的 target 定義
   （Makefile 編輯後行號會飄移，此測試可即時抓到）。
3. 重構防回歸：href 內不得殘留硬寫網域；``GH_REPO`` 全檔僅一處。
"""

import re
from pathlib import Path

import pytest

def _find_repo_root() -> Path:
    """向上尋找同時含 docs/index.html 與 Makefile 的目錄（不受測試位置影響）。"""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "index.html").is_file() and (
            parent / "Makefile"
        ).is_file():
            return parent
    raise RuntimeError("找不到 repo root（docs/index.html + Makefile）")


REPO_ROOT = _find_repo_root()
INDEX_HTML = REPO_ROOT / "docs" / "index.html"
MAKEFILE = REPO_ROOT / "Makefile"
GH_DOMAIN = "github.com/lastingyeh/adk-insurance-recommendation-agent"

HTML = INDEX_HTML.read_text(encoding="utf-8")
MAKEFILE_LINES = MAKEFILE.read_text(encoding="utf-8").splitlines()


def _href_targets(kind: str) -> set[str]:
    """收集 href="<kind>/main/<path>" 的相對路徑（去除 #anchor）。"""
    out = set()
    for m in re.finditer(rf'href="{kind}/main/([^"]+)"', HTML):
        out.add(m.group(1).split("#", 1)[0])
    return out


def _make_link_map() -> dict[str, str]:
    """解析 JS 內 MAKE_LINKS 物件：target -> 相對路徑。"""
    block = re.search(r"const MAKE_LINKS = \{(.*?)\};", HTML, re.DOTALL)
    assert block, "找不到 MAKE_LINKS 物件"
    return dict(re.findall(r'"([A-Za-z0-9_-]+)"\s*:\s*"([^"]+)"', block.group(1)))


def _makefile_line_defines(lineno: int, target: str) -> bool:
    """Makefile 第 lineno 行是否為 `target:` 定義。"""
    if not (1 <= lineno <= len(MAKEFILE_LINES)):
        return False
    return MAKEFILE_LINES[lineno - 1].startswith(f"{target}:")


# ── 1. blob 連結指向真實檔案 ──────────────────────────────────────────
@pytest.mark.parametrize("rel", sorted(_href_targets("blob")))
def test_blob_link_file_exists(rel):
    path = REPO_ROOT / rel
    assert path.is_file(), f"blob 連結指向不存在的檔案：{rel}"


# ── 2. tree 連結指向真實目錄 ──────────────────────────────────────────
@pytest.mark.parametrize("rel", sorted(_href_targets("tree")))
def test_tree_link_dir_exists(rel):
    path = REPO_ROOT / rel
    assert path.is_dir(), f"tree 連結指向不存在的目錄：{rel}"


# ── 3a. 內文 make 連結的 Makefile 行號正確 ────────────────────────────
_INLINE_MAKE = re.findall(
    r'href="blob/main/Makefile#L(\d+)"[^>]*>\s*<code>make ([a-z0-9-]+)</code>',
    HTML,
)


@pytest.mark.parametrize("lineno,target", [(int(n), t) for n, t in _INLINE_MAKE])
def test_inline_make_anchor_points_to_target(lineno, target):
    assert _makefile_line_defines(lineno, target), (
        f"內文連結 make {target} 指向 Makefile#L{lineno}，"
        f"但該行非 `{target}:` 定義"
    )


# ── 3b. MAKE_LINKS（bash 區塊用）的目標健全 ───────────────────────────
def _make_links_anchor_cases():
    cases = []
    for target, dest in _make_link_map().items():
        if dest.startswith("Makefile#L"):
            cases.append((target, int(dest.split("#L")[1])))
    return cases


def _make_links_file_cases():
    out = set()
    for dest in _make_link_map().values():
        if "#" not in dest:
            out.add(dest)
    return sorted(out)


@pytest.mark.parametrize("target,lineno", _make_links_anchor_cases())
def test_make_links_anchor_points_to_target(target, lineno):
    assert _makefile_line_defines(lineno, target), (
        f"MAKE_LINKS['{target}'] 指向 Makefile#L{lineno}，"
        f"但該行非 `{target}:` 定義"
    )


@pytest.mark.parametrize("rel", _make_links_file_cases())
def test_make_links_file_exists(rel):
    assert (REPO_ROOT / rel).is_file(), f"MAKE_LINKS 指向不存在的檔案：{rel}"


# ── 4. 重構防回歸 ─────────────────────────────────────────────────────
def test_no_hardcoded_domain_in_href():
    """href 內不得殘留硬寫網域（應為相對路徑）。"""
    leaks = re.findall(rf'href="https://{re.escape(GH_DOMAIN)}', HTML)
    assert not leaks, f"有 {len(leaks)} 個 href 仍硬寫網域，應改為相對路徑"


def test_single_gh_repo_source():
    """完整網域全檔僅出現一次（GH_REPO 設定常數）。"""
    count = HTML.count(GH_DOMAIN)
    assert count == 1, f"完整網域出現 {count} 次，應僅有 GH_REPO 一處"
