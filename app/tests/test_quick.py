#!/usr/bin/env python3
"""
快速测试脚本
测试新的 FastAPI 项目结构
"""
import sys
from pathlib import Path

# 注入项目根目录到 Python 搜索路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.tests.test_client import FastAPIClient


def test_basic():
    """基础测试"""
    print("=" * 60)
    print("🧪 FastAPI 项目结构测试")
    print("=" * 60)

    # 配置加载测试
    print("\n1️⃣ 测试配置加载...")
    try:
        from app.core.config import get_settings
        settings = get_settings()
        print(f"   ✅ 配置加载成功")
        print(f"      项目名称: {settings.PROJECT_NAME}")
        print(f"      版本: {settings.VERSION}")
        print(f"      LLM 模型: {settings.LLM_MODEL}")
    except Exception as e:
        print(f"   ❌ 配置加载失败: {e}")
        return False

    # 日志系统测试
    print("\n2️⃣ 测试日志系统...")
    try:
        from app.core.logging_config import setup_logging, get_logger
        setup_logging()
        logger = get_logger("test")
        logger.info("日志系统测试")
        print(f"   ✅ 日志系统正常")
    except Exception as e:
        print(f"   ❌ 日志系统失败: {e}")
        return False

    # FastAPI 应用构建测试
    print("\n3️⃣ 测试 FastAPI 应用...")
    try:
        from app.main import app
        print(f"   ✅ FastAPI 应用创建成功")
        print(f"      应用名称: {app.title}")
        print(f"      路由数量: {len(app.routes)}")
    except Exception as e:
        print(f"   ❌ FastAPI 应用失败: {e}")
        return False

    # 服务器连接测试
    print("\n4️⃣ 测试服务器连接...")
    client = FastAPIClient(server_url="http://localhost:5000")
    if client.check_health():
        print(f"   ✅ 服务器连接成功")
    else:
        print(f"   ⚠️  服务器未运行（这是正常的，如果你还没启动服务器）")
        print(f"      启动命令: uvicorn app.main:app --reload --port 5000")

    print("\n" + "=" * 60)
    print("✅ 基础测试完成！")
    print("=" * 60)
    return True


def test_document():
    """测试文档处理"""
    print("\n" + "=" * 60)
    print("📄 文档处理测试")
    print("=" * 60)

    # 读取可用测试文件
    test_data_dir = Path(__file__).parent / "data" / "test"
    # test_files = list(test_data_dir.glob("*.pdf"))
    extensions = ["*.docx", "*.doc", "*.pptx", "*.ppt", "*.pdf"]  # 支持多格式文档
    test_files = []
    for ext in extensions:
        test_files.extend(test_data_dir.glob(ext))
    
    if not test_files:
        print("\n⚠️  未找到测试文件")
        print(f"   请将测试文件放到: {test_data_dir}")
        return False

    test_file = test_files[0]
    print(f"\n测试文件: {test_file.name}")

    # 初始化客户端
    client = FastAPIClient(server_url="http://localhost:5000")

    # 服务器状态检查
    print("\n检查服务器状态...")
    if not client.check_health():
        print("\n❌ 服务器未运行，请先启动服务器:")
        print("   uvicorn app.main:app --reload --port 5000")
        return False

    # 文档处理测试
    print(f"\n开始处理文档...")
    success = client.process_document(
        str(test_file),
        poll_interval=2,
        max_wait_time=600
    )

    if success:
        print("\n✅ 文档处理测试成功！")
    else:
        print("\n❌ 文档处理测试失败")

    return success


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="FastAPI 项目测试脚本")
    parser.add_argument(
        "--full",
        action="store_true",
        help="运行完整测试（包括文档处理）"
    )

    args = parser.parse_args()

    # 基础能力测试
    if not test_basic():
        print("\n❌ 基础测试失败")
        sys.exit(1)

    # 完整流程测试
    if args.full:
        if not test_document():
            print("\n❌ 文档处理测试失败")
            sys.exit(1)
    else:
        print("\n💡 提示: 使用 --full 参数运行完整测试（包括文档处理）")
        print("   python app/tests/test_quick.py --full")

    print("\n🎉 所有测试通过！")


if __name__ == "__main__":
    main()
