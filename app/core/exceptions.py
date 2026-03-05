"""
自定义异常和异常处理器
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppException(Exception):
    """应用基础异常"""

    def __init__(self, code: int, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code


class NotFoundException(AppException):
    """资源不存在异常"""

    def __init__(self, message: str = "资源不存在"):
        super().__init__(code=404, message=message, status_code=404)


class BadRequestException(AppException):
    """请求参数错误异常"""

    def __init__(self, message: str = "请求参数错误"):
        super().__init__(code=400, message=message, status_code=400)


class ProcessingException(AppException):
    """处理失败异常"""

    def __init__(self, message: str = "处理失败"):
        super().__init__(code=500, message=message, status_code=500)


def register_exception_handlers(app: FastAPI):
    """注册全局异常处理器"""

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "data": None},
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": f"服务器内部错误: {str(exc)}", "data": None},
        )
