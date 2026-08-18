"""
Course generation module for vidforge.

Generates structured PowerPoint presentations from course topics or outlines.
Supports both LLM-generated outlines and manually-structured outlines.
"""

import json
from pathlib import Path
from datetime import datetime
from pptx import Presentation
from pptx.util import Inches

from .config import Config
from .llm import complete_json


class CourseGenerator:
    """Generates educational course PowerPoint presentations."""

    def __init__(self, output_root: Path = None):
        """Initialize course generator.

        Args:
            output_root: Root directory for course outputs. Defaults to output/courses/.
        """
        self.output_root = Path(output_root) / "courses" if output_root else Path("output/courses")
        self.output_root.mkdir(parents=True, exist_ok=True)

    def generate_outline(self, topic: str, levels: int = 3, progress=None) -> str:
        """
        Generate a structured course outline from a topic using LLM.

        Args:
            topic: The course topic (e.g., "Penetration Testing with Kali Linux & AI")
            levels: Number of hierarchical levels (1=flat, 3=weeks->sessions->topics)
            progress: Progress reporter (optional)

        Returns:
            Structured course outline as a string
        """
        if progress:
            progress.log("course", f"Generating course outline for: {topic}")

        cfg = Config.load()

        schema = {
            "type": "object",
            "properties": {
                "outline": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "duration": {"type": "string"}
            },
            "required": ["outline", "title", "description"]
        }

        result = complete_json(
            cfg,
            system="You are an expert course curriculum designer.",
            user=f"""Create a detailed, structured course outline for: {topic}

The outline should be hierarchical with {levels} levels of detail.
Use this format:
- Level 1: Course sections/weeks (if applicable)
  - Level 2: Sessions or subsections
    - Level 3: Individual topics

Be specific, educational, and practical. Include learning objectives and hands-on components where relevant.

Output as a clean, indented text structure suitable for parsing.""",
            schema=schema,
            name="course_outline"
        )

        return result.get("outline", "")

    def outline_to_slides(self, outline: str) -> list[dict]:
        """
        Parse a structured outline into slides.

        Each line with a colon becomes a slide (title:content format).
        Handles hierarchical indentation.

        Args:
            outline: Course outline text

        Returns:
            List of slide dicts with 'title', 'content', and 'level'
        """
        slides = []
        lines = outline.strip().split("\n")

        for line in lines:
            if not line.strip():
                continue

            # Count indentation level
            stripped = line.lstrip()
            level = (len(line) - len(stripped)) // 2

            # Remove list markers (-, •, *)
            if stripped and stripped[0] in '-•*':
                stripped = stripped[1:].strip()

            # Split on first colon if present
            if ":" in stripped:
                title, content = stripped.split(":", 1)
                title = title.strip()
                content = content.strip()
            else:
                title = stripped
                content = ""

            if title:
                slides.append({
                    "title": title,
                    "content": content,
                    "level": level
                })

        return slides

    def create_presentation(self,
                          topic: str = None,
                          outline: str = None,
                          output_file: str = None,
                          progress=None) -> Path:
        """
        Create a PowerPoint presentation from a topic or outline.

        Args:
            topic: Topic to generate from (generates outline automatically)
            outline: Pre-structured outline text (takes precedence over topic)
            output_file: Output filename. Defaults to topic-based slug.
            progress: Progress reporter (optional)

        Returns:
            Path to generated .pptx file
        """
        if progress:
            progress.log("course", "Starting course generation")

        # Generate or use provided outline
        if outline is None:
            if not topic:
                raise ValueError("Either topic or outline must be provided")
            outline = self.generate_outline(topic, progress=progress)
            title = topic
        else:
            title = topic or "Course"

        # Parse outline into slides
        slides_data = self.outline_to_slides(outline)

        if progress:
            progress.log("course", f"Creating presentation with {len(slides_data)} slides")

        # Create presentation
        prs = Presentation()
        prs.slide_width = Inches(10)
        prs.slide_height = Inches(7.5)

        # Title slide
        title_slide_layout = prs.slide_layouts[0]  # Title slide layout
        slide = prs.slides.add_slide(title_slide_layout)
        title_shape = slide.shapes.title
        subtitle_shape = slide.placeholders[1]

        title_shape.text = title
        subtitle_shape.text = f"Generated on {datetime.now().strftime('%B %d, %Y')}"

        # Content slides
        content_layout = prs.slide_layouts[1]  # Title and Content
        for slide_data in slides_data:
            slide = prs.slides.add_slide(content_layout)
            title_shape = slide.shapes.title
            body_shape = slide.placeholders[1]

            title_shape.text = slide_data["title"]

            # Set text frame content
            text_frame = body_shape.text_frame
            text_frame.clear()

            if slide_data["content"]:
                p = text_frame.paragraphs[0]
                p.text = slide_data["content"]
                p.level = 0

        # Save
        if output_file is None:
            # Generate slug from topic
            slug = (topic or "course").lower().replace(" ", "_")[:50]
            timestamp = datetime.now().strftime("%Y%m%d-%H%M")
            output_file = f"{timestamp}-{slug}.pptx"

        output_path = self.output_root / output_file
        prs.save(str(output_path))

        if progress:
            progress.log("course", f"✅ Course saved: {output_path}")

        # Save metadata
        metadata = {
            "topic": title,
            "slides": len(slides_data),
            "created": datetime.now().isoformat(),
            "output": str(output_path)
        }

        metadata_path = output_path.with_suffix(".json")
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)

        return output_path
