import base64
import uuid

# 阿里云 OSS SDK
from oss2 import Auth, Bucket
# 腾讯云 COS SDK
from qcloud_cos import CosConfig, CosS3Client
# Cloudflare R2 使用 boto3 S3 API 兼容
import boto3
from botocore.client import Config

from src.common.logger import get_logger

logger = get_logger("video_images_uploader")

class TempImageUploader:
    """
    支持阿里云OSS / 腾讯COS / Cloudflare R2 的Base64图片上传
    上传到 tmp_images 文件夹，url生命周期1小时
    """

    def __init__(self, provider: str, access_key_id: str, secret_access_key: str,
                 bucket_name: str, region: str = None, endpoint: str = None):
        """
        provider: "oss" / "cos" / "r2"
        统一参数:
        access_key_id, secret_access_key, bucket_name, region, endpoint
        """
        self.provider = provider.lower()
        self.tmp_folder = "tmp_images"
        self.url_expire_seconds = 60 * 60  # 1小时
        self.bucket_name = bucket_name

        if self.provider == "oss":
            auth = Auth(access_key_id, secret_access_key)
            self.client = Bucket(auth, endpoint, bucket_name)

        elif self.provider == "cos":
            config = CosConfig(Region=region,
                               SecretId=access_key_id,
                               SecretKey=secret_access_key)
            self.client = CosS3Client(config)

        elif self.provider == "r2":
            self.client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                config=Config(signature_version="s3v4"),
            )

    def upload_base64_image(self, base64_data: str) -> str:
        """
        上传Base64图片，返回可访问URL
        """
        # 自动判断图片格式
        img_format = "jpg"
        if base64_data.startswith("data:image/png"):
            img_format = "png"
        elif base64_data.startswith("data:image/jpeg"):
            img_format = "jpg"
        if "," in base64_data:
            base64_data = base64_data.split(",")[1]

        img_bytes = base64.b64decode(base64_data)
        filename = f"{uuid.uuid4().hex}.{img_format}"
        object_key = f"{self.tmp_folder}/{filename}"

        try:
            if self.provider == "oss":
                self.client.put_object(object_key, img_bytes)
                url = self.client.sign_url("GET", object_key, self.url_expire_seconds)
            elif self.provider == "cos":
                self.client.put_object(Bucket=self.bucket_name, Key=object_key, Body=img_bytes, ACL='private')
                url = self.client.get_presigned_url(Method='GET', Bucket=self.bucket_name, Key=object_key, Expired=self.url_expire_seconds)
            elif self.provider == "r2":
                self.client.put_object(Bucket=self.bucket_name, Key=object_key, Body=img_bytes)
                url = self.client.generate_presigned_url(
                    ClientMethod="get_object",
                    Params={"Bucket": self.bucket_name, "Key": object_key},
                    ExpiresIn=self.url_expire_seconds,
                )
            return url
        except Exception as e:
            logger.error(f"图片上传失败: {e}")
            return None