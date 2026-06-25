"""驗證稽核日誌雜湊鏈完整性。

用法：
    make audit-verify
    # 或
    uv run python -m scripts.verify_audit_chain

exit code：0 = 鏈完整；1 = 偵測到竄改或刪除；2 = 設定/連線錯誤。
"""

from __future__ import annotations

import asyncio
import sys

from app.config import load_runtime_config
from app.services.audit_log_service import AuditLogService, ChainVerificationResult


async def run_verification(service: AuditLogService) -> ChainVerificationResult:
    return await service.verify_chain()


async def _main_async() -> int:
    config = load_runtime_config()
    service = AuditLogService(
        db_url=config.audit_db_path,
        hash_salt=config.audit_hash_salt,
        retention_days=config.audit_retention_days,
        enabled=config.audit_enabled,
    )
    result = await run_verification(service)
    if result.ok:
        print(f"✅ 稽核鏈完整，共驗證 {result.checked_count} 筆。")
        return 0
    print(
        f"❌ 稽核鏈損壞：reason={result.reason} "
        f"於 chain_index={result.broken_chain_index}（共 {result.checked_count} 筆）。"
    )
    return 1


def main() -> None:
    try:
        sys.exit(asyncio.run(_main_async()))
    except Exception as exc:  # 設定或連線錯誤
        print(f"⚠️ 驗證執行失敗：{exc}")
        sys.exit(2)


if __name__ == "__main__":
    main()
