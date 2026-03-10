"""
FastAPI 服务器的客户端测试脚本
功能：
1. 提交文档处理任务，获取 task_id
2. 轮询任务状态直到完成
3. 将结果保存到 app/tests/results/task_id.json
"""
import requests
import base64
import time
import json
from pathlib import Path
from typing import Optional, Dict


class FastAPIClient:
    """FastAPI 服务器客户端"""

    def __init__(self, server_url: str = "http://localhost:8000"):
        self.server_url = server_url.rstrip('/')
        # 结果目录: app/tests/results/
        self.results_dir = Path(__file__).parent / "results"
        self.results_dir.mkdir(exist_ok=True)

    def submit_task(self, file_path: str) -> Optional[str]:
        """
        提交文档处理任务

        Args:
            file_path: 文档文件路径

        Returns:
            task_id: 任务ID，失败返回 None
        """
        print(f"\n{'='*60}")
        print(f"📄 提交文档: {file_path}")
        print(f"{'='*60}")

        # 文件存在性校验
        if not Path(file_path).exists():
            print(f"❌ 文件不存在: {file_path}")
            return None

        # 读取文件并编码为 Base64
        try:
            with open(file_path, "rb") as f:
                file_data = base64.b64encode(f.read()).decode()
            filename = Path(file_path).name
            print(f"✅ 文件读取成功，大小: {len(file_data)} 字节（Base64编码后）")
        except Exception as e:
            print(f"❌ 文件读取失败: {e}")
            return None

        # 提交任务（API 路径: /api/v1/document/process）
        try:
            print(f"📤 正在提交任务到服务器...")
            response = requests.post(
                f"{self.server_url}/api/v1/document/process",
                json={
                    "filedata": file_data,
                    "filename": filename
                },
                timeout=30
            )

            if response.status_code == 202:
                task_info = response.json()
                task_id = task_info["task_id"]
                print(f"✅ 任务提交成功！")
                print(f"   任务ID: {task_id}")
                print(f"   状态: {task_info['status']}")
                print(f"   消息: {task_info['message']}")
                return task_id
            else:
                print(f"❌ 任务提交失败: HTTP {response.status_code}")
                print(f"   响应: {response.text}")
                return None

        except requests.exceptions.Timeout:
            print(f"❌ 请求超时，请检查服务器是否运行")
            return None
        except requests.exceptions.ConnectionError:
            print(f"❌ 无法连接到服务器: {self.server_url}")
            print(f"   请确保服务器正在运行")
            return None
        except Exception as e:
            print(f"❌ 提交任务时发生错误: {e}")
            return None

    def get_task_status(self, task_id: str) -> Optional[Dict]:
        """
        查询任务状态

        Args:
            task_id: 任务ID

        Returns:
            任务状态信息，失败返回 None
        """
        try:
            response = requests.get(
                f"{self.server_url}/api/v1/document/status/{task_id}",
                timeout=10
            )

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                print(f"❌ 任务不存在: {task_id}")
                return None
            else:
                print(f"❌ 查询失败: HTTP {response.status_code}")
                return None

        except Exception as e:
            print(f"❌ 查询任务状态时发生错误: {e}")
            return None

    def wait_for_completion(
        self,
        task_id: str,
        poll_interval: int = 2,
        max_wait_time: int = 600
    ) -> Optional[Dict]:
        """
        等待任务完成

        Args:
            task_id: 任务ID
            poll_interval: 轮询间隔（秒）
            max_wait_time: 最大等待时间（秒）

        Returns:
            完成后的结果，失败返回 None
        """
        print(f"\n{'='*60}")
        print(f"🔄 开始轮询任务状态...")
        print(f"   轮询间隔: {poll_interval}秒")
        print(f"   最大等待: {max_wait_time}秒")
        print(f"{'='*60}\n")

        start_time = time.time()
        poll_count = 0

        while True:
            elapsed = time.time() - start_time

            # 超时检查
            if elapsed > max_wait_time:
                print(f"\n❌ 等待超时（{max_wait_time}秒），任务可能仍在处理中")
                print(f"   可以稍后使用以下命令查询:")
                print(f"   python app/tests/test_client.py --status {task_id}")
                return None

            # 轮询状态
            poll_count += 1
            status_data = self.get_task_status(task_id)

            if not status_data:
                print(f"❌ 无法获取任务状态")
                return None

            status = status_data.get("status")
            message = status_data.get("message", "")

            # 输出当前进度
            print(f"[{elapsed:.1f}s] 第 {poll_count} 次查询 - 状态: {status} - {message}")

            # 完成态
            if status == "completed":
                print(f"\n{'='*60}")
                print(f"✅ 任务完成！总耗时: {elapsed:.1f}秒")
                print(f"{'='*60}")
                return status_data

            # 失败态
            elif status == "failed":
                error = status_data.get("error", "Unknown error")
                print(f"\n{'='*60}")
                print(f"❌ 任务失败: {error}")
                print(f"{'='*60}")
                return None

            # 继续轮询
            elif status in ["pending", "processing"]:
                time.sleep(poll_interval)
                continue

            else:
                print(f"\n⚠️ 未知状态: {status}")
                return None

    def save_result(self, task_id: str, result: Dict) -> bool:
        """
        保存结果到文件

        Args:
            task_id: 任务ID
            result: 结果数据

        Returns:
            是否保存成功
        """
        try:
            result_file = self.results_dir / f"{task_id}.json"
            result_file.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            print(f"\n💾 结果已保存到: {result_file}")
            return True
        except Exception as e:
            print(f"\n❌ 保存结果失败: {e}")
            return False

    def print_result_summary(self, status_response: Dict):
        """打印结果摘要"""
        print(f"\n{'='*60}")
        print(f"📊 处理结果摘要")
        print(f"{'='*60}")

        # /status/{task_id} 返回结构：
        #   status_response["result"] = pipeline 返回对象（含 id/result/usage）
        #   status_response["result"]["result"] = 章节结果数组
        #   status_response["result"]["usage"] = token 统计
        pipeline_obj = status_response.get("result", {})
        keywords = pipeline_obj.get("result", [])
        usage = pipeline_obj.get("usage", {})

        print(f"\n📈 统计信息:")
        print(f"   - Token使用: {usage.get('total_tokens', 0)}")
        print(f"     • 输入: {usage.get('prompt_tokens', 0)}")
        print(f"     • 输出: {usage.get('completion_tokens', 0)}")

        # 章节统计
        print(f"\n📚 章节信息:")
        print(f"   - 章节数量: {len(keywords)}")

        for i, chapter in enumerate(keywords, 1):
            chapter_name = chapter.get("chapter", "未知章节")
            content_list = chapter.get("content", [])
            # content 结构: [{"basic": [...]}, {"keypoints": [...]}, ...]
            content = {}
            for module in content_list:
                content.update(module)
            print(f"\n   {i}. {chapter_name}")

            modules = {
                "basic": "基本要求",
                "keypoints": "教学重点",
                "difficulty": "教学难点",
                "politics": "课程思政"
            }

            for module_key, module_name in modules.items():
                if module_key in content:
                    items = content[module_key]
                    lexicon_count = sum(len(item.get("lexicon", [])) for item in items if isinstance(item, dict))
                    print(f"      ✓ {module_name} ({len(items)}个知识点, 词库共{lexicon_count}个)")
                else:
                    print(f"      ✗ {module_name} (缺失)")

        print(f"\n{'='*60}")

    def process_document(
        self,
        file_path: str,
        poll_interval: int = 2,
        max_wait_time: int = 600
    ) -> bool:
        """
        完整的文档处理流程

        Args:
            file_path: 文档文件路径
            poll_interval: 轮询间隔（秒）
            max_wait_time: 最大等待时间（秒）

        Returns:
            是否处理成功
        """
        # 提交任务
        task_id = self.submit_task(file_path)
        if not task_id:
            return False

        # 等待完成
        result = self.wait_for_completion(task_id, poll_interval, max_wait_time)
        if not result:
            return False

        # 保存结果
        if not self.save_result(task_id, result):
            return False

        # 打印摘要
        self.print_result_summary(result)

        return True

    def query_task(self, task_id: str) -> bool:
        """
        查询已存在的任务

        Args:
            task_id: 任务ID

        Returns:
            是否查询成功
        """
        print(f"\n{'='*60}")
        print(f"🔍 查询任务: {task_id}")
        print(f"{'='*60}")

        status_data = self.get_task_status(task_id)
        if not status_data:
            return False

        status = status_data.get("status")
        print(f"\n状态: {status}")
        print(f"消息: {status_data.get('message', '')}")

        if status == "completed":
            # 写入本地结果文件
            self.save_result(task_id, status_data)
            self.print_result_summary(status_data)
            return True
        elif status == "failed":
            print(f"错误: {status_data.get('error', 'Unknown error')}")
            return False
        else:
            print(f"\n任务仍在处理中，请稍后再查询")
            return False

    def check_health(self) -> bool:
        """检查服务器健康状态"""
        try:
            response = requests.get(f"{self.server_url}/health", timeout=5)
            if response.status_code == 200:
                data = response.json()
                print(f"✅ 服务器健康")
                print(f"   服务: {data.get('service', 'Unknown')}")
                print(f"   版本: {data.get('version', 'Unknown')}")
                print(f"   任务数: {data.get('tasks_count', 0)}")
                return True
            else:
                print(f"❌ 服务器异常: HTTP {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ 无法连接到服务器: {e}")
            return False


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(
        description="FastAPI 服务器客户端测试工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 文档处理
  python app/tests/test_client.py app/tests/data/海洋学院-SR113026-海洋油气地质学.pdf

  # 指定服务器地址
  python app/tests/test_client.py document.pdf --server http://192.168.1.100:8000

  # 查询已存在的任务
  python app/tests/test_client.py --status chatcmpl-xxxxx

  # 服务器健康检查
  python app/tests/test_client.py --health
        """
    )

    parser.add_argument(
        "file",
        nargs="?",
        help="要处理的文档文件路径"
    )
    parser.add_argument(
        "--server",
        default="http://localhost:8000",
        help="服务器地址 (默认: http://localhost:8000)"
    )
    parser.add_argument(
        "--status",
        default=None,
        metavar="TASK_ID",
        help="查询指定任务的状态"
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="检查服务器健康状态"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=2,
        help="轮询间隔（秒，默认: 2）"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="最大等待时间（秒，默认: 600）"
    )

    args = parser.parse_args()

    # 初始化客户端
    client = FastAPIClient(server_url=args.server)

    # 健康检查
    if args.health:
        print(f"\n{'='*60}")
        print(f"检查服务器健康状态")
        print(f"{'='*60}\n")
        client.check_health()
        return

    # 查询任务状态
    if args.status:
        success = client.query_task(args.status)
        exit(0 if success else 1)

    # 文档处理入口
    if args.file:
        # 先检查服务器
        print(f"\n{'='*60}")
        print(f"检查服务器状态")
        print(f"{'='*60}\n")
        if not client.check_health():
            print(f"\n请先启动服务器:")
            print(f"  uvicorn app.main:app --reload --port 8000")
            exit(1)

        # 执行文档处理
        success = client.process_document(
            args.file,
            poll_interval=args.interval,
            max_wait_time=args.timeout
        )
        exit(0 if success else 1)

    # 没有提供参数，显示帮助
    parser.print_help()


if __name__ == "__main__":
    main()
