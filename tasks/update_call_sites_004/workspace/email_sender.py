from report_formatter import render


def build_alert(level):
    """构造一条告警邮件正文。"""
    return f"告警\n{render('水位', level)}"
