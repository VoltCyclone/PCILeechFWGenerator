from typing import Any, Dict

from textual.screen import ModalScreen
from textual.widgets import Input, Select


class SearchFilterDialog(ModalScreen[Dict[str, Any]]):
    """Modal dialog for searching and filtering devices"""


    def _clear_all_filters(self) -> None:
        try:
            self.query_one("#device-search", Input).value = ""
            self.query_one("#class-filter", Select).value = "all"
            self.query_one("#status-filter", Select).value = "all"
            self.query_one("#score-filter", Input).value = "0.0"
        except Exception:
            pass

    def _get_filter_criteria(self) -> Dict[str, Any]:
        try:
            score_text = self.query_one("#score-filter", Input).value
            min_score = float(score_text) if score_text else 0.0
        except Exception:
            min_score = 0.0

        return {
            "device_search": self.query_one("#device-search", Input).value,
            "class_filter": self.query_one("#class-filter", Select).value,
            "status_filter": self.query_one("#status-filter", Select).value,
            "min_score": min_score,
        }
