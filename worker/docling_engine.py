"""Docling's converters. Imported only when needed, since importing it is heavy."""

from functools import lru_cache
from io import BytesIO
from typing import Any, override

from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import ConversionStatus, DocumentStream, InputFormat
from docling.datamodel.pipeline_options import (
    OcrMacOptions,
    PdfPipelineOptions,
    VlmPipelineOptions,
)
from docling.datamodel.vlm_model_specs import GRANITEDOCLING_MLX
from docling.document_converter import (
    DocumentConverter,
    FormatOption,
    ImageFormatOption,
    PdfFormatOption,
)
from docling.pipeline.vlm_pipeline import VlmPipeline
from docling_core.transforms.chunker.hierarchical_chunker import (
    ChunkingDocSerializer,
    ChunkingSerializerProvider,
)
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.base import BaseTokenizer
from docling_core.transforms.serializer.base import (
    BaseDocSerializer,
    SerializationResult,
)
from docling_core.transforms.serializer.common import create_ser_result
from docling_core.transforms.serializer.markdown import (
    MarkdownPictureSerializer,
    MarkdownTableSerializer,
)
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.picture.picture import PictureItem

from worker import picture_text
from worker.content import estimate_tokens

LAYOUT_THREADS = 4
CHUNK_TOKENS = 512


def stream_of(name: str, data: BytesIO) -> DocumentStream:
    return DocumentStream(name=name, stream=data)


def _pdf_options() -> PdfPipelineOptions:
    # Layout on the CPU keeps both processes under 6 GB, as measured.
    return PdfPipelineOptions(
        accelerator_options=AcceleratorOptions(
            device=AcceleratorDevice.CPU, num_threads=LAYOUT_THREADS
        ),
        do_ocr=True,
        ocr_options=OcrMacOptions(replace_broken_text_layer=True),
        do_table_structure=True,
        do_picture_classification=True,
        generate_picture_images=True,
        # Kept for which text OCR read, which shown_text.py never leaves out.
        generate_parsed_pages=True,
        images_scale=2.0,
    )


@lru_cache(maxsize=1)
def full_converter() -> DocumentConverter:
    options = _pdf_options()
    formats: dict[InputFormat, FormatOption] = {
        InputFormat.PDF: PdfFormatOption(pipeline_options=options),
        InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
    }
    return DocumentConverter(format_options=formats)


def page_reading_converter() -> DocumentConverter:
    options = VlmPipelineOptions(vlm_options=GRANITEDOCLING_MLX, images_scale=2.0)
    formats: dict[InputFormat, FormatOption] = {
        InputFormat.PDF: PdfFormatOption(
            pipeline_cls=VlmPipeline, pipeline_options=options
        )
    }
    return DocumentConverter(format_options=formats)


def read_or_none(
    converter: DocumentConverter, source: DocumentStream, page: int
) -> Any:
    """One page's document, or None when Docling's pipeline failed on it: a page
    reader can describe a picture with no area, which Docling can't crop."""
    result = converter.convert(source, page_range=(page, page), raises_on_error=False)
    if result.status == ConversionStatus.FAILURE:
        return None
    return result.document


def unload_converters() -> None:
    full_converter.cache_clear()


class ApproximateTokenizer(BaseTokenizer):
    """Counts tokens by characters: close enough for a 512-token target, and it needs
    no model. The embedder truncates at 8,192, far above the 1,024 cap."""

    max_tokens: int = CHUNK_TOKENS

    def count_tokens(self, text: str) -> int:
        return estimate_tokens(text)

    def get_max_tokens(self) -> int:
        return self.max_tokens

    def get_tokenizer(self) -> object:
        return self


class _PictureWithItsText(MarkdownPictureSerializer):
    """A picture as Docling writes it, then the text drawn inside it, so a chart's
    labels and values are searchable before any model reads the chart."""

    @override
    def serialize(
        self,
        *,
        item: PictureItem,
        doc_serializer: BaseDocSerializer,
        doc: DoclingDocument,
        **kwargs: Any,
    ) -> SerializationResult:
        result = super().serialize(
            item=item, doc_serializer=doc_serializer, doc=doc, **kwargs
        )
        drawn = picture_text.inside(item, doc)
        if not drawn:
            return result
        text = "\n".join(part for part in (result.text, drawn) if part)
        return create_ser_result(text=text, span_source=item)


class _ChunkSerializers(ChunkingSerializerProvider):
    def get_serializer(self, doc):  # type: ignore[no-untyped-def]
        return ChunkingDocSerializer(
            doc=doc,
            table_serializer=MarkdownTableSerializer(),
            picture_serializer=_PictureWithItsText(),
        )


def chunker() -> HybridChunker:
    return HybridChunker(
        tokenizer=ApproximateTokenizer(),
        merge_peers=True,
        repeat_table_header=True,
        serializer_provider=_ChunkSerializers(),
    )
