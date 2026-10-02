"""カスタムルールのサンプル: 日付が土日でないこと.

    fieldcheck --plugin samples/plugins/weekday_rule.py validate data.csv -r rules.yaml

ルールファイルでの使い方:

    - id: EXP-100
      type: weekday
      field: 申請日
      severity: warning
"""

from field_validation.rules import Rule, register
from field_validation.values import ValueTypeError, is_blank, to_date


@register
class Weekday(Rule):
    """日付が平日（月〜金）であること."""

    type_name = "weekday"
    params_schema = {"field": {"type": "string"}}
    required_params = ["field"]
    field_params = ["field"]

    def check(self, loc, rec):
        f = self.spec["field"]
        v = rec.get(f)
        if is_blank(v):
            return
        try:
            d = to_date(v)
        except ValueTypeError:
            return
        if d.weekday() >= 5:
            yield self.finding(loc, f, f"{f} が土日です", expected="平日", actual=d.isoformat())
