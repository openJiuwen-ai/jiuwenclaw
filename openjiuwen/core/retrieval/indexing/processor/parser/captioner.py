import os
import shutil
import base64
from typing import List, Optional
import mimetypes

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model

# The captioning prompt below is directly informed by:
# - General multimodal RAG conventions of declaring purpose ("for retrieval")
# - Emphasis on qualitative summaries of document visuals for retrieval (https://openreview.net/forum?id=ogjBpZ8uSi)
# - "Beyond Text"'s instruction to produce summaries well optimized for retrieval (https://arxiv.org/abs/2410.21943)
# - Embodied-RAG's dense detail captioning strategy used during indexing

IMAGE_CAPTION_PROMPT = (
    "You are an assistant specialized in document and image analysis. "
    "Your task is to provide a detailed, qualitative description of the provided image "
    "so that it can be embedded and used for semantic retrieval. "
    "Describe all visible content including text, figures, charts, tables, diagrams, and layout. "
    "Focus on what the image conveys and means, not just what it literally depicts. "
    "Do not include any preamble — output only the description."
)
SAVED_IMAGE_DIR = "images"

# Adding missing MIME type for .jfif files mapping them to "image/jpeg"
mimetypes.add_type("image/jpeg", ".jfif", strict=True)


class ImageCaptioner:
    _supported_llm_client = ["gpt-4o", "gpt-5", "qwen3-vl"]

    def __init__(self, llm_client: Optional[Model] = None):
        self.llm_client = llm_client
        self._is_llm_supported()

    @staticmethod
    def cp_image(image_loc: str, target_dir: str = SAVED_IMAGE_DIR) -> str:
        """Utility function to read an image file and return its base64-encoded string representation."""
        if not os.path.exists(image_loc):
            raise FileNotFoundError(f"Image not found at: {image_loc}")

        # Ensure target dir exists
        os.makedirs(target_dir, exist_ok=True)

        img_base = os.path.basename(image_loc)
        dest_path = os.path.join(target_dir, img_base)

        try:
            # Only copy if destination is different
            if os.path.abspath(image_loc) != os.path.abspath(dest_path):
                shutil.copy2(image_loc, dest_path)
        except Exception as e:
            logger.warning(f"Failed to save copy of {image_loc} to {dest_path}: {e}")
            # fall back to original path if copy failed
            dest_path = image_loc
        return dest_path

    def _is_llm_supported(self) -> None:
        """Checks if the current LLM client is supported for image captioning.

        Returns:
            bool: True if supported, False otherwise
        """
        if self.llm_client is None:
            logger.warning(
                f"Image captioning is disabled for empty {self.llm_client=}. Please ensure an appropriate VLM is used."
            )
        else:
            if not any(
                [
                    self.llm_client.model_config.model_name.startswith(base_model_name)
                    for base_model_name in self._supported_llm_client
                ]
            ):
                logger.warning(
                    f"The provided {self.llm_client=} may not be fully supported for imaging captioning.",
                    "Please ensure an appropriate VLM is used.",
                )

    async def _llm_call_async(self, image_loc: str) -> str:
        """
        Invokes the LLM to generate a caption for the image at the given `image_loc`.
        Args:
            image_loc: Local file path to the image
        Returns:
            Generated caption or empty `str` if invocation fails
        """
        if self.llm_client is None:
            return ""

        try:
            # Detect MIME type from file extension
            mime_type, _ = mimetypes.guess_type(image_loc)
            if not mime_type:
                mime_type = "image/png"  # fallback default
                logger.warning(f"Could not determine MIME type for {image_loc=}, using {mime_type}")

            logger.info(f"Calling LLM for image captioning for {image_loc=}")
            with open(image_loc, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("utf-8")

            resp = await self.llm_client.invoke(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": IMAGE_CAPTION_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
                            },
                        ],
                    }
                ]
            )
            return resp.content if hasattr(resp, "content") else str(resp)
        except Exception as e:
            logger.warning(
                f"LLM-based caption for {image_loc=} with invocation failed for {image_loc}: {e}"
            )
            return ""

    async def caption_images(self, image_locs: List[str]) -> List[str]:
        """
        Accepts a list of image file paths, generate captions for each using the LLM, and return the list of captions.

        Args:
            image_locs (List[str]): list of image file paths

        Returns:
            List[str]: list of generated captions
        """
        captions = []
        for image_loc in image_locs:
            if os.path.exists(image_loc):
                caption = await self._llm_call_async(image_loc)
            else:
                logger.warning(f"Image file {image_loc} does not exist, skipping captioning.")
                caption = ""
            captions.append(caption)
        return captions
