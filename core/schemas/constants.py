from PIL import Image

# 初始化 PIL 以注册所有支持的格式
Image.init()

# 支持的文件格式（动态获取）
SUPPORTED_FILE_FORMATS_WITH_DOT = tuple(
    ext.lower() for ext in Image.registered_extensions().keys()
)
SUPPORTED_FILE_FORMATS = tuple(
    ext.lstrip(".") for ext in SUPPORTED_FILE_FORMATS_WITH_DOT
)

# 提示词参数列表
PARAMS_LIST = [
    "min_images",
    "max_images",
    "refer_images",
    "image_size",
    "aspect_ratio",
    "google_search",
    "negative_prompt",
    "num_inference_steps",
    "guidance_scale",
    "seed",
    "preset_append",
    "gather_mode",
    "providers",
    "n",
    "partial_images",
    "size",
    "url",
    "sub_brain",
    "moderation",
    "capability",
    "quality",
    "fps",
    "with_audio",
    "watermark_enabled",
]

# 部分平台对单张图片大小有限制，超过限制需要作为文件发送
MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
# 预计算 Base64 长度阈值 (向下取整)，base64编码约为原始数据的4/3倍
MAX_SIZE_B64_LEN = int(MAX_SIZE_BYTES * 4 / 3)
