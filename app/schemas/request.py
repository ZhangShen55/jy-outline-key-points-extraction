"""
请求模型
"""
from typing import Any, Dict, List
from pydantic import BaseModel, Field


class ProcessRequest(BaseModel):
    """文档处理请求"""

    filedata: str = Field(..., description="Base64编码的文件内容")
    filename: str = Field(..., description="文件名（包含后缀）")

    class Config:
        json_schema_extra = {
            "example": {
                "filedata": "JVBERi0xLjQKJeLjz9MKMSAwIG9iago8PC9UeXBlL0NhdGFsb2cvUGFnZXMgMiAwIFI+PgplbmRvYmoKMiAwIG9iago8PC9UeXBlL1BhZ2VzL0NvdW50IDEvS2lkc1szIDAgUl0+PgplbmRvYmoKMyAwIG9iago8PC9UeXBlL1BhZ2UvTWVkaWFCb3hbMCAwIDYxMiA3OTJdL1BhcmVudCAyIDAgUi9SZXNvdXJjZXM8PC9Gb250PDwvRjEgNCAwIFI+Pj4+L0NvbnRlbnRzIDUgMCBSPj4KZW5kb2JqCjQgMCBvYmoKPDwvVHlwZS9Gb250L1N1YnR5cGUvVHlwZTEvQmFzZUZvbnQvSGVsdmV0aWNhPj4KZW5kb2JqCjUgMCBvYmoKPDwvTGVuZ3RoIDQ0Pj4Kc3RyZWFtCkJUCi9GMSA0OCBUZgoxMDAgNzAwIFRkCihIZWxsbyBXb3JsZCkgVGoKRVQKZW5kc3RyZWFtCmVuZG9iagp4cmVmCjAgNgowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwMTUgMDAwMDAgbiAKMDAwMDAwMDA2NiAwMDAwMCBuIAowMDAwMDAwMTI1IDAwMDAwIG4gCjAwMDAwMDAyNDQgMDAwMDAgbiAKMDAwMDAwMDMxNyAwMDAwMCBuIAp0cmFpbGVyCjw8L1NpemUgNi9Sb290IDEgMCBSPj4Kc3RhcnR4cmVmCjQxMAolJUVPRgo=",
                "filename": "example.pdf",
            }
        }


class LessonAnalyzeRequest(BaseModel):
    """课堂语音转写内容教案大纲匹配分析"""

    syllabus_result: Dict[str, Any] = Field(..., description="大纲提取结果（含 course + result 字段）")
    text_segments: List[Dict[str, Any]] = Field(..., description="语音转写段落列表 [{text, bg, ed}, ...]")
    filename: str = Field(..., description="课程文件名（用于任务标识）")
