import datetime
import os
import re
from pathlib import Path
import zipfile

import pandas as pd
import pytz

from infra.utils import get_logger


class LiveCandleExporter:
    """
    Minimal live candle export helper.

    Scope is intentionally limited to tickers detected as 급등 candidates.
    Export prefers the fullest available candle history by merging runtime
    cache with a fresh KIS candle fetch at export time.
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

        normalized = self._normalize_candle_dataframe(df)
        if normalized.empty:
            return

        existing = self.runtime_candle_cache.get(ticker, {})
        merged = self._merge_candle_dataframes(existing.get("df"), normalized)
        if merged.empty:
            return

        self.runtime_candle_cache[ticker] = {
            "df": merged,
            "exchange": exchange or existing.get("exchange") or self.registered_candidates.get(ticker, {}).get("exchange"),
            "source": "runtime_cache",
        }

    def export_for_date(self, date_str=None):
        target_date = self._normalize_date_str(date_str)
        saved_files = []
        manifest_rows = []

        for ticker in sorted(self.registered_candidates.keys()):
            payload = self._get_export_dataframe(ticker)
            df = payload["df"]
            source = payload["source"]
            exchange = payload["exchange"]

            if df is None or df.empty:
                self.logger.warning(f"[Live Export] No candle data available for {ticker}")
                manifest_rows.append({
                    "date": target_date,
                    "ticker": ticker,
                    "csv_path": "",
                    "source": "missing",
                    "exchange": exchange or "",
                    "rows": 0,
                    "min_date": "",
                    "min_time": "",
                    "max_date": "",
                    "max_time": "",
                    "used_runtime_cache": False,
                    "used_kis_refetch": False,
                    "status": "no_data",
                })
                continue

            normalized = self._normalize_candle_dataframe(df)
            if normalized.empty:
                self.logger.warning(f"[Live Export] Normalized candle data empty for {ticker}")
                manifest_rows.append({
                    "date": target_date,
                    "ticker": ticker,
                    "csv_path": "",
                    "source": source,
                    "exchange": exchange or "",
                    "rows": 0,
                    "min_date": "",
                    "min_time": "",
                    "max_date": "",
                    "max_time": "",
                    "used_runtime_cache": payload["used_runtime_cache"],
                    "used_kis_refetch": payload["used_kis_refetch"],
                    "status": "normalized_empty",
                })
                continue

            file_path = self.live_candles_dir / f"{target_date.replace('-', '')}_{ticker}.csv"
            normalized.to_csv(file_path, index=False, encoding="utf-8")
            saved_files.append(file_path)

            first_row = normalized.iloc[0]
            last_row = normalized.iloc[-1]
            manifest_rows.append({
                "date": target_date,
                "ticker": ticker,
                "csv_path": str(file_path),
                "source": source,
                "exchange": exchange or "",
                "rows": len(normalized),
                "min_date": first_row["date"],
                "min_time": first_row["time"],
                "max_date": last_row["date"],
                "max_time": last_row["time"],
                "used_runtime_cache": payload["used_runtime_cache"],
                "used_kis_refetch": payload["used_kis_refetch"],
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
            self.logger.warning(f"[Live Export] Telegram bot unavailable. Local zip kept: {zip_path}")
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
                f"[Live Export] date={target_date} files={len(files)} zip={zip_path} telegram_sent={sent}"
            )
        else:
            self.logger.warning(f"[Live Export] No files exported for {target_date}")

        return {
            "date": target_date,
            "files": [str(p) for p in files],
            "zip_path": str(zip_path) if zip_path else "",
            "telegram_sent": sent,
            "manifest_rows": manifest_rows,
        }

    def _get_export_dataframe(self, ticker):
        runtime_entry = self.runtime_candle_cache.get(ticker, {})
        runtime_df = runtime_entry.get("df")
        runtime_exchange = runtime_entry.get("exchange") or self.registered_candidates.get(ticker, {}).get("exchange")

        exchange_candidates = []
        if runtime_exchange:
            exchange_candidates.append(runtime_exchange)
        for exchange in ["NAS", "NYS", "AMS"]:
            if exchange not in exchange_candidates:
                exchange_candidates.append(exchange)

        refetch_df = None
        refetch_exchange = None
        for exchange in exchange_candidates:
            try:
                df = self.kis.get_minute_candles(exchange, ticker, limit=1200)
            except Exception as e:
                self.logger.warning(f"[Live Export] Fetch failed {ticker} {exchange}: {e}")
                continue

            if df is not None and not df.empty:
                refetch_df = df.copy()
                refetch_exchange = exchange
                break

        used_runtime_cache = runtime_df is not None and not runtime_df.empty
        used_kis_refetch = refetch_df is not None and not refetch_df.empty

        merged = self._merge_candle_dataframes(runtime_df, refetch_df)
        if merged.empty:
            return {
                "df": None,
                "source": "missing",
                "exchange": refetch_exchange or runtime_exchange,
                "used_runtime_cache": used_runtime_cache,
                "used_kis_refetch": used_kis_refetch,
            }

        if used_runtime_cache and used_kis_refetch:
            source = "runtime_cache_plus_kis_refetch"
        elif used_runtime_cache:
            source = "runtime_cache_only"
        elif used_kis_refetch:
            source = "kis_refetch_only"
        else:
            source = "missing"

        return {
            "df": merged,
            "source": source,
            "exchange": refetch_exchange or runtime_exchange,
            "used_runtime_cache": used_runtime_cache,
            "used_kis_refetch": used_kis_refetch,
        }

    def _normalize_candle_dataframe(self, df):
        normalized = df.copy()
        normalized.columns = [str(col).lower() for col in normalized.columns]

        if "date" not in normalized.columns or "time" not in normalized.columns:
            raise ValueError("Candle dataframe must contain date/time columns for export")

        for column in self.CSV_COLUMNS:
            if column not in normalized.columns:
                normalized[column] = ""

        normalized["date"] = normalized["date"].apply(self._normalize_export_date_value)
        normalized["time"] = normalized["time"].apply(self._normalize_export_time_value)

        normalized["_sort_key"] = (
            normalized["date"].str.replace("-", "", regex=False)
            + normalized["time"].str.zfill(6)
        )
        normalized = normalized.sort_values("_sort_key", kind="stable")
        normalized = normalized.drop_duplicates(subset=["date", "time"], keep="last")
        normalized = normalized.drop(columns=["_sort_key"])

        return normalized[self.CSV_COLUMNS].reset_index(drop=True)

    def _merge_candle_dataframes(self, *dataframes):
        frames = []
        for frame in dataframes:
            if frame is None:
                continue
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                frames.append(self._normalize_candle_dataframe(frame))

        if not frames:
            return pd.DataFrame(columns=self.CSV_COLUMNS)

        merged = pd.concat(frames, ignore_index=True)
        merged["date"] = merged["date"].apply(self._normalize_export_date_value)
        merged["time"] = merged["time"].apply(self._normalize_export_time_value)
        merged["_sort_key"] = (
            merged["date"].str.replace("-", "", regex=False)
            + merged["time"].str.zfill(6)
        )
        merged = merged.sort_values("_sort_key", kind="stable")
        merged = merged.drop_duplicates(subset=["date", "time"], keep="last")
        merged = merged.drop(columns=["_sort_key"])
        return merged[self.CSV_COLUMNS].reset_index(drop=True)

    def _normalize_export_date_value(self, value):
        if pd.isna(value):
            return ""

        if isinstance(value, pd.Timestamp):
            return value.strftime("%Y%m%d")

        text = str(value).strip().replace(".0", "")
        if not text:
            return ""

        if len(text) == 8 and text.isdigit():
            return text

        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            return text.replace("-", "")

        parsed = pd.to_datetime(text, errors="coerce")
        if pd.isna(parsed):
            return text
        return parsed.strftime("%Y%m%d")

    def _normalize_export_time_value(self, value):
        if pd.isna(value):
            return ""

        text = str(value).strip().replace(".0", "")
        text = re.sub(r"\D", "", text)
        if not text:
            return ""

        if len(text) <= 4:
            return text.zfill(4)

        return text.zfill(6)

    def _normalize_date_str(self, date_str=None):
        if date_str:
            return str(date_str)
        now_et = datetime.datetime.now(pytz.timezone("America/New_York"))
        return now_et.strftime("%Y-%m-%d")
