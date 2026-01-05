from abc import ABC, abstractmethod

class IStrategy(ABC):
    @abstractmethod
    def calculate_indicators(self, df):
        """지표 계산 (EMA, SMA 등)"""
        pass

    @abstractmethod
    def check_entry(self, df):
        """
        진입 신호 확인
        Return: {'price': float, 'comment': str} or None
        """
        pass

    @abstractmethod
    def check_exit(self, df, entry_price, max_price, entry_time):
        """
        청산 신호 확인 (Trailing Stop, Time Limit 등)
        Return: {'type': 'MARKET'|'LIMIT', 'price': float, 'reason': str} or None
        """
        pass