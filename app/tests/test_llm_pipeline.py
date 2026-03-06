"""
测试LLM Pipeline

使用方法:
    python app/tests/test_llm_pipeline.py --file /path/to/document.pdf
"""

import asyncio
import argparse
import json
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.llm_pipeline import run_llm_pipeline


async def main():
    parser = argparse.ArgumentParser(description="测试LLM Pipeline")
    parser.add_argument("--file", "-f", required=True, help="要处理的文档路径")
    parser.add_argument("--output", "-o", help="输出结果到文件（JSON格式）")
    
    args = parser.parse_args()
    
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"❌ 文件不存在: {file_path}")
        return 1
    
    print(f"🚀 开始处理文档: {file_path.name}")
    print(f"   文件路径: {file_path.absolute()}")
    print()
    
    try:
        # 运行LLM Pipeline
        result = await run_llm_pipeline(file_path, orig_name=file_path.stem)
        
        print("✅ 处理完成!")
        print()
        
        # 打印结果摘要
        print("=" * 60)
        print("📊 处理结果摘要")
        print("=" * 60)
        print(f"模型: {result.get('model', 'N/A')}")
        print(f"处理时间: {result['result']['process_time_ms']}ms")
        print(f"完成时间: {result['result']['finished_time']}")
        print()
        print("📈 Token使用统计:")
        print(f"   Prompt tokens: {result['usage']['prompt_tokens']}")
        print(f"   Completion tokens: {result['usage']['completion_tokens']}")
        print(f"   Total tokens: {result['usage']['total_tokens']}")
        print()
        print("📚 提取章节数:")
        keywords = result['result']['keywords']
        print(f"   共 {len(keywords)} 个章节")
        for i, chapter in enumerate(keywords, 1):
            chapter_name = chapter.get('chapter', f'章节{i}')
            content_count = len(chapter.get('content', []))
            print(f"   - {chapter_name} ({content_count} 个模块)")
        
        # 保存到文件
        if args.output:
            output_path = Path(args.output)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print()
            print(f"💾 结果已保存到: {output_path.absolute()}")
        
        return 0
        
    except Exception as e:
        print(f"\n❌ 处理失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
