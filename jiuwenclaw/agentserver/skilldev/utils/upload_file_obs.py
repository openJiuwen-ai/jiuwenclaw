import uuid
import logging


logger = logging.getLogger(__name__)


class UploadFileOSMS(object):
    def __init__(self):
        self.trace_id = str(uuid.uuid4())[:16]
        self.is_permanent_storage_public = False

    async def upload_file(self, file_path: str, is_permanent_storage_public=False):
        try:
            self.is_permanent_storage_public = is_permanent_storage_public
            updload_file_info = await self.upload_file_and_get_info(file_path)
            if isinstance(updload_file_info, str):
                return None
            return updload_file_info["url"]
        except Exception as e:
            logger.error("上传文件到osms失败")

    async def upload_file_and_get_info(self, file_path: str):
        return ""
