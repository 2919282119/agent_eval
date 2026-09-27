from report_formatter import render


def reading_payload(pressure):
    """上传到接口的读数。"""
    return {"text": render("气压", pressure)}
