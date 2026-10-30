import json
from unittest.mock import Mock

import pytest
import requests

from smart_invest.ai import AIError, generate_ai_analysis


@pytest.fixture(autouse=True)
def clear_ai_environment(monkeypatch):
    for name in ("INVEST_AI_API_KEY", "INVEST_AI_BASE_URL", "INVEST_AI_MODEL"):
        monkeypatch.delenv(name, raising=False)


def mock_ai(monkeypatch, status=200, payload=None):
    response = Mock(status_code=status)
    response.json.return_value = payload or {"choices": [{"message": {"content": "历史波动较高，应核查样本区间。"}}]}
    post = Mock(return_value=response)
    monkeypatch.setattr("smart_invest.ai.requests.post", post)
    monkeypatch.setenv("INVEST_AI_API_KEY", "test-only-secret")
    return post


def test_no_key_does_not_claim_to_generate_ai_report(monkeypatch):
    post = Mock()
    monkeypatch.setattr("smart_invest.ai.requests.post", post)
    with pytest.raises(AIError, match="INVEST_AI_API_KEY"):
        generate_ai_analysis("AAPL", {"annual_volatility": 0.3}, "风险如何？")
    post.assert_not_called()


def test_request_has_timeout_no_redirect_and_no_key_in_prompt(monkeypatch):
    post = mock_ai(monkeypatch)
    result = generate_ai_analysis("aapl", {"annual_volatility": 0.3, "price_basis": "adjusted"}, "风险如何？")
    args, kwargs = post.call_args
    assert args[0] == "https://api.openai.com/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer test-only-secret"
    assert kwargs["timeout"] == (5, 45)
    assert kwargs["allow_redirects"] is False
    assert kwargs["json"]["model"] == "gpt-4.1-mini"
    assert "test-only-secret" not in json.dumps(kwargs["json"])
    user_data = json.loads(kwargs["json"]["messages"][1]["content"])
    assert user_data["symbol"] == "AAPL"
    assert user_data["summary"]["annual_volatility"] == 0.3
    assert "不得预测未来股价" in kwargs["json"]["messages"][0]["content"]
    assert "未读取新闻、财报或实时行情" in result
    assert "历史波动较高" in result
    assert "test-only-secret" not in result


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.openai.com/v1",
        "https://evil.example/v1",
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://169.254.169.254/v1",
        "https://api.openai.com.evil.example/v1",
        "https://secret@api.openai.com/v1",
        "https://api.openai.com/v1?key=secret",
        "https://api.openai.com/v1#fragment",
        "https://api.openai.com:8443/v1",
        "https://api.openai.com:invalid/v1",
        "https://api.openai.com/other",
        "https://api.openai.com/v1/../../admin",
    ],
)
def test_unsafe_endpoint_rejected_before_key_can_be_sent(monkeypatch, base_url):
    post = mock_ai(monkeypatch)
    monkeypatch.setenv("INVEST_AI_BASE_URL", base_url)
    with pytest.raises(AIError):
        generate_ai_analysis("AAPL", {"annual_volatility": 0.3}, "风险如何？")
    post.assert_not_called()


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.openai.com/v1/", "https://api.openai.com/v1/chat/completions"),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"),
        ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"),
        ("https://api.deepseek.com", "https://api.deepseek.com/chat/completions"),
        ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/chat/completions"),
    ],
)
def test_supported_official_endpoints(monkeypatch, base_url, expected):
    post = mock_ai(monkeypatch)
    monkeypatch.setenv("INVEST_AI_BASE_URL", base_url)
    monkeypatch.setenv("INVEST_AI_MODEL", "qwen-plus")
    generate_ai_analysis("0700.HK", {"annual_volatility": 0.3}, "风险如何？")
    assert post.call_args.args[0] == expected
    assert post.call_args.kwargs["json"]["model"] == "qwen-plus"


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_service_failures_are_clear_and_do_not_echo_response_secrets(monkeypatch, status):
    post = mock_ai(monkeypatch, status=status)
    post.return_value.text = "test-only-secret should not be echoed"
    with pytest.raises(AIError) as caught:
        generate_ai_analysis("AAPL", {"annual_volatility": 0.3}, "风险如何？")
    assert "test-only-secret" not in str(caught.value)


@pytest.mark.parametrize("error", [requests.Timeout("timeout"), requests.ConnectionError("failure")])
def test_network_failure_raises_an_ai_error(monkeypatch, error):
    post = mock_ai(monkeypatch)
    post.side_effect = error
    with pytest.raises(AIError, match="超时|连接"):
        generate_ai_analysis("AAPL", {"annual_volatility": 0.3}, "风险如何？")


@pytest.mark.parametrize(
    "summary",
    [{}, {"volatility": float("nan")}, {"volatility": float("inf")}, {"invalid": object()}, {"large": "x" * 25_001}],
)
def test_invalid_statistics_rejected_before_request(monkeypatch, summary):
    post = mock_ai(monkeypatch)
    with pytest.raises(AIError):
        generate_ai_analysis("AAPL", summary, "风险如何？")
    post.assert_not_called()


@pytest.mark.parametrize("question", ["", " ", "x" * 2001, None])
def test_missing_or_excessive_question_is_rejected(monkeypatch, question):
    post = mock_ai(monkeypatch)
    with pytest.raises(AIError):
        generate_ai_analysis("AAPL", {"annual_volatility": 0.3}, question)
    post.assert_not_called()


def test_invalid_model_rejected_before_request(monkeypatch):
    post = mock_ai(monkeypatch)
    monkeypatch.setenv("INVEST_AI_MODEL", "model\ninvalid")
    with pytest.raises(AIError, match="MODEL"):
        generate_ai_analysis("AAPL", {"annual_volatility": 0.3}, "风险如何？")
    post.assert_not_called()


@pytest.mark.parametrize("payload", [{"choices": []}, {"choices": [{"message": {"content": None}}]}, {"choices": [{"message": {"content": " "}}]}])
def test_invalid_model_response_is_not_shown_as_report(monkeypatch, payload):
    mock_ai(monkeypatch, payload=payload)
    with pytest.raises(AIError, match="有效"):
        generate_ai_analysis("AAPL", {"annual_volatility": 0.3}, "风险如何？")


def test_invalid_json_from_service_is_handled(monkeypatch):
    post = mock_ai(monkeypatch)
    post.return_value.json.side_effect = ValueError("invalid JSON")
    with pytest.raises(AIError, match="有效"):
        generate_ai_analysis("AAPL", {"annual_volatility": 0.3}, "风险如何？")


def test_portfolio_review_sends_only_given_context_with_portfolio_rules(monkeypatch):
    from smart_invest.ai import generate_portfolio_review

    post = mock_ai(monkeypatch)
    context = {"positions": [{"symbol": "AAPL", "weight": 0.6}], "portfolio": {"annualized_volatility": 0.2}}
    result = generate_portfolio_review(context, "集中度如何？")
    kwargs = post.call_args.kwargs
    assert "不得预测未来价格" in kwargs["json"]["messages"][0]["content"]
    sent = json.loads(kwargs["json"]["messages"][1]["content"])
    assert sent == {"portfolio": context, "question": "集中度如何？"}
    assert "test-only-secret" not in json.dumps(kwargs["json"]) + result
    assert "未读取新闻、财报、估值或实时行情" in result


@pytest.mark.parametrize("context", [{}, {"positions": []}, None])
def test_portfolio_review_requires_positions(monkeypatch, context):
    from smart_invest.ai import generate_portfolio_review

    post = mock_ai(monkeypatch)
    with pytest.raises(AIError, match="缺少组合统计"):
        generate_portfolio_review(context, "问题")
    post.assert_not_called()
