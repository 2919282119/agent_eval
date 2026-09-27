from report_formatter import render


def temperature_panel():
    """仪表盘上的温度面板。"""
    return render("温度", 23)
