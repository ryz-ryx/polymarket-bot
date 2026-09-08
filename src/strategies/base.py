from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

class BaseStrategy(ABC):
    """
    Base class for trading strategies.
    Any strategy code discussed with Claude or developed here inherits from this.
    """
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def evaluate(self, spot_price: float, momentum: float, market_info: Dict[str, Any], order_book: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Evaluates current signals.
        Returns trade instruction dict or None:
        {
            'outcome': 'YES' or 'NO',
            'estimated_prob': float,
            'target_price': float,
            'reason': str
        }
        """
        pass