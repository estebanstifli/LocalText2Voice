import pytest
from app.core.storyboard_generation_errors import generation_error_details,missing_image_error
from app.core.video_storyboard_comfyui import _save_litellm_image,VideoStoryboardImageError

@pytest.mark.parametrize('reason',['SAFETY','IMAGE_SAFETY','PROHIBITED_CONTENT'])
def test_empty_gemini_response_preserves_safety_reason(tmp_path,reason):
    response={'data':[],'candidates':[{'finishReason':reason}],'api_key':'secret','b64_json':'huge'}
    with pytest.raises(VideoStoryboardImageError) as result:
        _save_litellm_image(response,tmp_path/'out.png',1)
    details=generation_error_details(result.value)
    assert details['code']=='moderation_blocked' and not details['retryable']
    assert reason in details['error'] and 'secret' not in details['error'] and 'huge' not in details['error']

def test_empty_response_does_not_invent_moderation():
    detail=generation_error_details(RuntimeError(missing_image_error({'data':[]},'No image')))
    assert detail['code']=='generation_failed'

@pytest.mark.parametrize('message,code,retry',[('HTTP 429: rate limit','rate_limit',True),('HTTP 503: overloaded','provider_unavailable',True),('insufficient_quota','quota_or_billing',False),('request timed out','timeout',True)])
def test_failure_types(message,code,retry):
    detail=generation_error_details(RuntimeError(message))
    assert detail['code']==code and detail['retryable']==retry
