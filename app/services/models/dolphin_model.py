import torch
from transformers import AutoProcessor, VisionEncoderDecoderModel
from PIL import Image
import base64, io
import os
import cv2
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.utils.utils import convert_pdf_to_images, prepare_image, parse_layout_string, process_coordinates
from app.core.logging_config import get_logger
logger = get_logger(__name__)


# -------------- 第二部分：DOLPHIN 模型相关（来自 parser_page_hf.py） --------------
class DOLPHIN:
    def __init__(self, model_id_or_path):
        """Initialize the Hugging Face model"""
        self.processor = AutoProcessor.from_pretrained(model_id_or_path)
        self.model = VisionEncoderDecoderModel.from_pretrained(model_id_or_path)
        self.model.eval()
        self.device = "cuda:0"  # 对应 CUDA_VISIBLE_DEVICES 中的第 0 张 GPU
        self.model.to(self.device)
        # self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = self.model.half()

        self.tokenizer = self.processor.tokenizer

    def chat(self, prompt, image):
        """Process an image or batch of images with the given prompt(s)"""
        is_batch = isinstance(image, list)

        if not is_batch:
            images = [image]
            prompts = [prompt]
        else:
            images = image
            prompts = prompt if isinstance(prompt, list) else [prompt] * len(images)

        batch_inputs = self.processor(images, return_tensors="pt", padding=True)
        batch_pixel_values = batch_inputs.pixel_values.half().to(self.device)

        prompts = [f"<s>{p} <Answer/>" for p in prompts]
        batch_prompt_inputs = self.tokenizer(
            prompts,
            add_special_tokens=False,
            return_tensors="pt"
        )

        batch_prompt_ids = batch_prompt_inputs.input_ids.to(self.device)
        batch_attention_mask = batch_prompt_inputs.attention_mask.to(self.device)

        outputs = self.model.generate(
            pixel_values=batch_pixel_values,
            decoder_input_ids=batch_prompt_ids,
            decoder_attention_mask=batch_attention_mask,
            min_length=1,
            max_length=4096,
            pad_token_id=self.tokenizer.pad_token_id,
            eos_token_id=self.tokenizer.eos_token_id,
            use_cache=True,
            bad_words_ids=[[self.tokenizer.unk_token_id]],
            return_dict_in_generate=True,
            do_sample=False,
            num_beams=1,
            repetition_penalty=1.1,
            temperature=1.0
        )

        sequences = self.tokenizer.batch_decode(outputs.sequences, skip_special_tokens=False)

        results = []
        for i, sequence in enumerate(sequences):
            cleaned = sequence.replace(prompts[i], "").replace("<pad>", "").replace("</s>", "").strip()
            results.append(cleaned)

        if not is_batch:
            return results[0]
        return results

# def process_document(document_path: str, model, save_dir, max_batch_size=None, max_workers=2, gpus="0,1"):
#     """
#     并行处理文档的每一页，控制 GPU 使用和并行数。
#
#     Args:
#         document_path: 文档路径
#         model: Dolphin 模型
#         save_dir: 保存结果目录
#         max_batch_size: 每批次处理大小
#         max_workers: 并行子进程数
#         gpus: 使用的 GPU id 列表，例如 "0,1"
#     """
#     import concurrent.futures
#     import multiprocessing as mp
#
#     # PDF 转图片
#     file_ext = os.path.splitext(document_path)[1].lower()
#     if file_ext == '.pdf':
#         images = convert_pdf_to_images(document_path)
#         if not images:
#             raise Exception(f"Failed to convert PDF {document_path} to images")
#     else:
#         images = [Image.open(document_path).convert("RGB")]
#
#     base_name = os.path.splitext(os.path.basename(document_path))[0]
#
#     # 每页的任务参数
#     task_args = []
#     for i, img in enumerate(images):
#         # 指定 GPU 卡轮询
#         gpu_id = gpus.split(",")[i % len(gpus.split(","))]
#         task_args.append((img, model, save_dir, f"{base_name}_page_{i+1:03d}", max_batch_size, False, gpu_id))
#
#     # 设置 spawn
#     mp.set_start_method('spawn', force=True)
#
#     results = []
#
#     # 使用可控并行数
#     with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers, mp_context=mp.get_context('spawn')) as executor:
#         for res in executor.map(_process_single_image_wrapper_with_gpu, task_args):
#             results.append(res)
#
#     # 汇总结果
#     all_results = [{"page_number": i + 1, "elements": res} for i, res in enumerate(results)]
#     return all_results

def _process_single_image_wrapper_with_gpu(args):
    """
    Wrapper for process pool,同时设置子进程的CUDA_VISIBLE_DEVICES
    args: (image, model, save_dir, image_name, max_batch_size, save_individual, gpu_id)
    """
    image, model, save_dir, image_name, max_batch_size, save_individual, gpu_id = args
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
    device = f"cuda:0"  # 对应子进程看到的第0张显卡
    model.model.to(device)
    model.device = device
    return process_single_image(image, model, save_dir, image_name, max_batch_size, save_individual)

# def process_document(document_path: str, model, save_dir, max_batch_size=None):
#     # 并行处理 相当于加了一个用 spawn 启动的进程池并行调用
#     file_ext = os.path.splitext(document_path)[1].lower()
#     if file_ext == '.pdf':
#         images = convert_pdf_to_images(document_path)
#         if not images:
#             raise Exception(f"Failed to convert PDF {document_path} to images")
#     else:
#         images = [Image.open(document_path).convert("RGB")]
#
#     base_name = os.path.splitext(os.path.basename(document_path))[0]
#     task_args = [
#         (img, model, save_dir, f"{base_name}_page_{i+1:03d}", max_batch_size, False)
#         for i, img in enumerate(images)
#     ]
#
#     # 使用 spawn 启动子进程，避免 CUDA fork 问题
#     mp.set_start_method('spawn', force=True)
#     with concurrent.futures.ProcessPoolExecutor(mp_context=mp.get_context('spawn')) as executor:
#         results = list(executor.map(_process_single_image_wrapper, task_args))
#
#     all_results = [
#         {"page_number": i + 1, "elements": res}
#         for i,  res in enumerate(results)
#     ]
#     # combined_json_path = save_combined_pdf_results(all_results, document_path, save_dir)
#     return all_results


def process_document(document_path: str, model, save_dir, max_batch_size=None):
    """
    串行处理文档的每一页，避免并行导致的 CUDA OOM。
    """
    file_ext = os.path.splitext(document_path)[1].lower()

    # 将 PDF 转为图像列表，或直接读取单张图像
    if file_ext == '.pdf':
        images = convert_pdf_to_images(document_path)
        if not images:
            raise Exception(f"Failed to convert PDF {document_path} to images")
    else:
        images = [Image.open(document_path).convert("RGB")]

    base_name = os.path.splitext(os.path.basename(document_path))[0]
    task_args = [
        (img, model, save_dir, f"{base_name}_page_{i+1:03d}", max_batch_size, False)
        for i, img in enumerate(images)
    ]

    # 串行执行（逐页处理，显存安全）
    results = []
    for args in task_args:
        res = _process_single_image_wrapper(args)
        results.append(res)

    # 汇总结果
    all_results = [
        {"page_number": i + 1, "elements": res}
        for i, res in enumerate(results)
    ]

    # combined_json_path = save_combined_pdf_results(all_results, document_path, save_dir)
    return all_results


def _process_single_image_wrapper(args):
    """Wrapper function for process pool"""
    return process_single_image(*args)


def process_single_image(image, model, save_dir, image_name, max_batch_size=None, save_individual=False):
    """Process a single image (either from file or converted from PDF page)

    Args:
        image: PIL Image object
        model: DOLPHIN model instance
        save_dir: Directory to save results
        image_name: Name for the output file
        max_batch_size: Maximum batch size for processing
        save_individual: Whether to save individual results (False for PDF pages)

    Returns:
        Tuple of (json_path, recognition_results)
    """
    # Stage 1: Page-level layout and reading order parsing
    layout_output = model.chat("Parse the reading order of this document. Do not omit any text, including directory pages, outlines, or subheadings. Treat directory pages as normal text.", image)

    # Stage 2: Element-level content parsing
    padded_image, dims = prepare_image(image)  # 填充为正方形，填充值为 000
    recognition_results = process_elements(layout_output, padded_image, dims, model, max_batch_size, save_dir, image_name)
    # Save outputs only if requested (skip for PDF pages)
    json_path = None
    if save_individual:
        # Create a dummy image path for save_outputs function
        dummy_image_path = f"{image_name}.jpg"  # Extension doesn't matter, only basename is used
        json_path = save_outputs(recognition_results, dummy_image_path, save_dir)

    return recognition_results


def process_elements(layout_results, padded_image, dims, model, max_batch_size, save_dir=None, image_name=None):
    """Parse all document elements with parallel decoding"""
    layout_results = parse_layout_string(layout_results)

    # Store text and table elements separately
    text_elements = []  # Text elements
    figure_results = []  # Image elements (no processing needed)
    previous_box = None
    reading_order = 0

    # Collect elements to process and group by type
    for bbox, label in layout_results:
        try:
            # Adjust coordinates
            x1, y1, x2, y2, orig_x1, orig_y1, orig_x2, orig_y2, previous_box = process_coordinates(
                bbox, padded_image, dims, previous_box
            )

            # Crop and parse element
            cropped = padded_image[y1:y2, x1:x2]
            if cropped.size > 0 and cropped.shape[0] > 3 and cropped.shape[1] > 3:
                pil_crop = Image.fromarray(cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB))
                if label == "fig":
                    # ---------- 改动开始：不落盘，直接转 base64 ----------
                    buf = io.BytesIO() # 创建内存缓冲区
                    pil_crop.save(buf, format='JPEG') # 将图片保存到内存缓冲区
                    b64 = base64.b64encode(buf.getvalue()).decode() # 编码为Base64字符串
                    figure_results.append(
                        {
                            "label": label,
                            "text": "![Figure](data:image/jpeg;base64,...)",
                            "figure_b64": b64,
                            "bbox": [orig_x1, orig_y1, orig_x2, orig_y2],
                            "reading_order": reading_order,
                        }
                    )
                    # ---------- 改动结束 ----------
                elif label == "tab":
                    # ---------- 新增：表格也走 base64 ----------
                    buf = io.BytesIO()
                    pil_crop.save(buf, format='JPEG')
                    b64 = base64.b64encode(buf.getvalue()).decode()
                    figure_results.append({
                        "label": label,
                        "text": "![Table](data:image/jpeg;base64,...)",
                        "table_b64": b64,
                        "bbox": [orig_x1, orig_y1, orig_x2, orig_y2],
                        "reading_order": reading_order,

                    })
                else:  # Text elements
                    # Prepare element for parsing
                    element_info = {
                        "crop": pil_crop,
                        "label": label,
                        "bbox": [orig_x1, orig_y1, orig_x2, orig_y2],
                        "reading_order": reading_order,
                    }

                    # Group by type
                    text_elements.append(element_info)

            reading_order += 1

        except Exception as e:
            logger.info(f"Error processing bbox with label {label}: {str(e)}")
            continue
    # Initialize results list
    recognition_results = figure_results.copy()

    # Process text elements (in batches)
    if text_elements:
        text_results = process_element_batch(text_elements, model, "Read text in the image.", max_batch_size)
        recognition_results.extend(text_results)

    # Process table elements (in batches) —— 现在里面只存 base64，后续 parser_text.py 会二次调用视觉模型
    # if table_elements:
    #     table_results = process_element_batch(table_elements, model, "Parse the table in the image.", max_batch_size)
    #     recognition_results.extend(table_results)

    # Sort elements by reading order
    recognition_results.sort(key=lambda x: x.get("reading_order", 0))

    return recognition_results


def process_element_batch(elements, model, prompt, max_batch_size=None):
    """Process elements of the same type in batches"""
    results = []

    # Determine batch size
    batch_size = len(elements)
    if max_batch_size is not None and max_batch_size > 0:
        batch_size = min(batch_size, max_batch_size)

    # Process in batches
    for i in range(0, len(elements), batch_size):
        batch_elements = elements[i:i+batch_size]
        crops_list = [elem["crop"] for elem in batch_elements]

        # Use the same prompt for all elements in the batch
        prompts_list = [prompt] * len(crops_list)

        # Batch inference
        batch_results = model.chat(prompts_list, crops_list)

        # Add results
        for j, result in enumerate(batch_results):
            elem = batch_elements[j]
            # 如果是 table，不真正跑dolphin模型，只留空文本，因为后面喂给vllm效果更好
            results.append({
                "label": elem["label"],
                "bbox": elem["bbox"],
                "text": result.strip(),
                "reading_order": elem["reading_order"],
            })

    return results