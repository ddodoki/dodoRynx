# utils/download_fonts.py
"""
Protomaps 오프라인 폰트 번들 다운로드 유틸리티.
앱 설치 또는 최초 실행 시 1회만 실행.

다운로드 출처:
  https://github.com/protomaps/basemaps-assets
  제공 폰트: Noto Sans Regular / Medium / Italic
  라이선스: SIL Open Font License
"""
from __future__ import annotations
import io
import shutil
import urllib.request
import zipfile
from pathlib import Path

# Protomaps basemaps-assets ZIP (전체 저장소)
_ASSETS_ZIP_URL = (
    "https://github.com/protomaps/basemaps-assets"
    "/archive/refs/heads/main.zip"
)
# 저장소 ZIP 내부 폰트 경로 접두어
_ZIP_FONT_PREFIX = "basemaps-assets-main/fonts/"


def download_protomaps_fonts(dest_dir: Path,
                             timeout: int = 60) -> tuple[bool, str]:
    """
    Protomaps 전체 폰트 PBF를 dest_dir 에 다운로드한다.

    dest_dir 구조 (다운로드 후):
      dest_dir/
        Noto Sans Regular/  0-255.pbf, 256-511.pbf, ...
        Noto Sans Medium/   0-255.pbf, 256-511.pbf, ...
        Noto Sans Italic/   0-255.pbf, ...

    Returns (True, "") or (False, 오류 메시지)
    """
    dest_dir = Path(dest_dir)

    # ── 이미 다운로드됐는지 빠른 확인 ──────────────────────────────
    marker = dest_dir / ".fonts_downloaded"
    if marker.exists():
        return True, "already downloaded"

    print(f"[Font] Protomaps 폰트 ZIP 다운로드 중... ({_ASSETS_ZIP_URL})")
    try:
        with urllib.request.urlopen(_ASSETS_ZIP_URL, timeout=timeout) as resp:
            zip_data = resp.read()
    except Exception as e:
        return False, f"다운로드 실패: {e}"

    print(f"[Font] ZIP 크기: {len(zip_data) // 1024} KB — 압축 해제 중...")

    extracted = 0
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for entry in zf.infolist():
                name = entry.filename

                # fonts/ 디렉터리의 .pbf 파일만 추출
                if not name.startswith(_ZIP_FONT_PREFIX):
                    continue
                if not name.endswith(".pbf"):
                    continue

                # ZIP 내부: basemaps-assets-main/fonts/Noto Sans Regular/0-255.pbf
                # → 로컬:   dest_dir/Noto Sans Regular/0-255.pbf
                rel = name[len(_ZIP_FONT_PREFIX):]  # "Noto Sans Regular/0-255.pbf"
                out_path = dest_dir / rel
                out_path.parent.mkdir(parents=True, exist_ok=True)

                with zf.open(entry) as src, open(out_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted += 1

    except zipfile.BadZipFile as e:
        return False, f"ZIP 파싱 실패: {e}"
    except Exception as e:
        return False, f"압축 해제 실패: {e}"

    if extracted == 0:
        return False, "ZIP에서 PBF 파일을 찾지 못했습니다 (ZIP 구조 변경 가능성)"

    # 완료 마커 기록
    marker.write_text(f"downloaded: {extracted} files")
    print(f"[Font] 완료: {extracted}개 PBF 파일 → {dest_dir}")
    return True, f"{extracted} files"


def verify_fonts(dest_dir: Path) -> dict[str, bool]:
    """필수 폰트 파일 존재 여부 확인."""
    required = {
        "Noto Sans Regular/0-255.pbf",
        "Noto Sans Medium/0-255.pbf",
    }
    return {
        f: (dest_dir / f).exists()
        for f in required
    }
