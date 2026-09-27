from pipeline import check_overheat


def overheat_alert(raw_reading):
    temperature, too_hot = check_overheat(raw_reading)
    if too_hot:
        return f"过热告警：{temperature:.1f}°C"
    return f"温度正常：{temperature:.1f}°C"
