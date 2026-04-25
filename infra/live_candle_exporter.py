import datetime
import os
from pathlib import Path
import zipfile

import pandas as pd
import pytz

from infra.utils import get_logger


class LiveCandleExporter:
    """
    Minimal live candle export helper.

    Scope is intentionally limited to tickers detected as 급등 candidates.
    Export prefers runtime-observed candle DataFrames when available and
    falls back to a fresh KIS candle fetch at export time.
    """

    CSV_COLUMNS = ["date", "time", "open", "high", "low", "close", "volume"]

    def __init__(self, kis_api, telegram_bot=None, base_dir=None):
        self.kis = kis_api
        self.bot = telegram_bot
        self.logger = get_logger("LiveCandleExporter")

        root = Path(base_dir or os.getcwd())
        self.live_candles_dir = root / "logs" / "live_candles"
        self.live_exports_dir = root / "logs" / "live_exports"
        self.live_candles_dir.mkdir(parents=True, exist_ok=True)
        self.live_exports_dir.mkdir(parents=True, exist_ok=True)

        self.registered_candidates = {}
        self.runtime_candle_cache = {}

    def reset_session(self):
        self.registered_candidates.clear()
        self.runtime_candle_cache.clear()

    def register_candidate(self, ticker, exchange=None, detected_at=None):
        if not ticker:
            return

        if detected_at is None:
            detected_at = datetime.datetime.now(pytz.timezone("America/New_York"))

        meta = self.registered_candidates.get(ticker, {})
        meta.setdefault("detected_at", detected_at.isoformat())
        if exchange:
            meta["exchange"] = exchange
        self.registered_candidates[ticker] = meta

    def update_runtime_candles(self, ticker, df, exchange=None):
        if not ticker or ticker not in self.registered_candidates:
            return
        if df is None or df.empty:
            return

        self.runtime_candle_cache[ticker] = {
            "df": df.copy(),
            "exchange": exchange or self.registered_candidates.get(ticker, {}).get("exchange"),
            "source": "runtime_cache",
        }

    def export_for_date(self, date_str=None):
        target_date = self._normalize_date_str(date_str)
        saved_files = []
        manifest_rows = []

        for ticker in sorted(self.registered_candidates.keys()):
            df, source, exchange = self._get_export_dataframe(ticker)
            if df is None or df.empty:
                self.logger.warning(f"⚠️ [Live Export] No candle data available for {ticker}")
                manifest_rows.append({
                    "date": target_date,
                    "ticker": ticker,
                    "csv_path": "",
                    "source": "missing",
                    "exchange": exchange or "",
                    "rows": 0,
                    "status": "no_data",
                })
                continue

            normalized = self._normalize_candle_dataframe(df)
            if normalized.empty:
                self.logger.warning(f"⚠️ [Live Export] Normalized candle data empty for {ticker}")
                manifest_rows.append({
                    "date": target_date,
                    "ticker": ticker,
                    "csv_path": "",
                    "source": source,
                    "exchange": exchange or "",
                    "rows": 0,
                    "status": "normalized_empty",
                })
                continue

            file_path = self.live_candles_dir / f"{target_date.replace('-', '')}_{ticker}.csv"
            normalized.to_csv(file_path, index=False, encoding="utf-8")
            saved_files.append(file_path)
            manifest_rows.append({
                "date": target_date,
                "ticker": ticker,
                "csv_path": str(file_path),
                "source": source,
                "exchange": exchange or "",
                "rows": len(normalized),
                "status": "saved",
            })

        return saved_files, manifest_rows

    def zip_export(self, date_str, files):
        target_date = self._normalize_date_str(date_str)
        zip_path = self.live_exports_dir / f"{target_date.replace('-', '')}_live_candles_export.zip"

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in files:
                if Path(file_path).exists():
                    zf.write(file_path, arcname=Path(file_path).name)

        return zip_path

    def send_export_to_telegram(self, zip_path, date_str=None):
        if not zip_path or not Path(zip_path).exists():
            return False
        if not self.bot:
            self.logger.warning(f"⚠️ [Live Export] Telegram bot unavailable. Local zip kept: {zip_path}")
            return False

        target_date = self._normalize_date_str(date_str)
        caption = f"[Live Candle Export] {target_date} 급등 후보 KIS candles"
        return bool(self.bot.send_document(str(zip_path), caption=caption))

    def export_zip_and_send(self, date_str=None):
        target_date = self._normalize_date_str(date_str)
        files, manifest_rows = self.export_for_date(target_date)
        zip_path = None
        sent = False

        if files:
            zip_path = self.zip_export(target_date, files)
            sent = self.send_export_to_telegram(zip_path, target_date)
            self.logger.info(
                f"📦 [Live Export] date={target_date} files={len(files)} zip={zip_path} telegram_sent={sent}"
            )
        else:
            self.logger.warning(f"⚠️ [Live Export] No files exported for {target_date}")

        return {
            "date": target_date,
            "files": [str(p) for p in files],
            "zip_path": str(zip_path) if zip_path else "",
            "telegram_sent": sent,
            "manifest_rows": manifest_rows,
        }

    def _get_export_dataframe(self, ticker):
        cached = self.runtime_candle_cache.get(ticker)
        if cached and cached.get("df") is not None and not cached["df"].empty:
            return cached["df"].copy(), cached.get("source", "runtime_cache"), cached.get("exchange")

        exchange_candidates = []
        registered_exchange = self.registered_candidates.get(ticker, {}).get("exchange")
        if registered_exchange:
            exchange_candidates.append(registered_exchange)
        for exchange in ["NAS", "NYS", "AMS"]:
            if exchange not in exchange_candidates:
                exchange_candidates.append(exchange)

        for exchange in exchange_candidates:
            try:
                df = self.kis.get_minute_candles(exchange, ticker, limit=1200)
            except Exception as e:
                self.logger.warning(f"⚠️ [Live Export] Fetch failed {ticker} {exchange}: {e}")
                continue

            if df is not None and not df.empty:
                return df.copy(), "kis_refetch", exchange

        return None, "missing", registered_exchange

    def _normalize_candle_dataframe(self, df):
        normalized = df.copy()
        normalized.columns = [str(col).lower() for col in normalized.columns]

        if "date" not in normalized.columns or "time" not in normalized.columns:
            raise ValueError("Candle dataframe must contain date/time columns for export")

        for column in self.CSV_COLUMNS:
            if column not in normalized.columns:
                normalized[column] = ""

        normalized["date"] = normalized["date"].astype(str)
        normalized["time"] = (
            normalized["time"]
            .apply(lambda x: "" if pd.isna(x) else str(x).split(".")[0].zfill(4))
        )

        return normalized[self.CSV_COLUMNS].drop_duplicates(subset=["date", "time"], keep="last").reset_index(drop=True)

    def _normalize_date_str(self, date_str=None):
        if date_str:
            return str(date_str)
        now_et = datetime.datetime.now(pytz.timezone("America/New_York"))
        return now_et.strftime("%Y-%m-%d")
