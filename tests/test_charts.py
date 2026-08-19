from agentlab.charts import verdict_chart_png


def test_chart_is_reasonable_png():
    png = verdict_chart_png("truncate", "codes_first", 0.61, 0.88, 0.27, 0.21, 0.33)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert 1_000 < len(png) < 250_000
