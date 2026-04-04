"""Step 4: Script skeleton — create a detailed script outline with talking points.

Supports long-form content (30-90 min) by splitting into multiple Claude calls.
Injects expert notes from project data for unique content.
"""

import json
import logging

from steps.base import BaseStep

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — профессиональный сценарист YouTube-видео.
Напиши полный скелет сценария для длинного видео.

Скелет ОБЯЗАТЕЛЬНО содержит следующие структурные блоки:
1. ХУК (первые 30 секунд) — мощная зацепка, провокационный вопрос или шок-факт
2. ИНТРО (15-30 сек) — представление темы и обещание ценности
3. ОСНОВНЫЕ БЛОКИ (3-8 блоков по 2-8 минут) — раскрытие темы по частям
4. КУЛЬМИНАЦИЯ (1-2 минуты) — главный инсайт, «ага-момент»
5. CTA + АУТРО (30-60 сек) — призыв к действию и завершение

ВАЖНО: Каждый блок должен содержать РАЗВЁРНУТЫЕ talking_points — не 2-3 пункта, а 5-10 конкретных тезисов,
фактов, примеров, чтобы ведущий мог говорить полноценно {target_minutes} минут.

Между каждым блоком укажи тип перехода (transition).
В каждом основном блоке укажи retention-хук для удержания зрителя.

Ответ ВСЕГДА в формате JSON (без markdown-блоков):
{{
  "title": "заголовок",
  "total_duration_minutes": {target_minutes},
  "blocks": [
    {{
      "block_number": 1,
      "block_type": "hook | intro | main | climax | cta_outro",
      "name": "название блока",
      "timestamp_start": "0:00",
      "duration_seconds": 30,
      "format": "talking_head | b_roll | screen_record | mixed",
      "talking_points": ["пункт 1", "пункт 2", "пункт 3", "пункт 4", "пункт 5"],
      "key_phrase": "ключевая фраза, которую ведущий должен произнести",
      "visual_direction": "описание того, что на экране",
      "audio_notes": "музыка, звуковые эффекты",
      "retention_hook": "хук удержания (для основных блоков)",
      "transition_to_next": "тип и описание перехода к следующему блоку"
    }}
  ],
  "key_messages": ["ключевое сообщение 1", "ключевое сообщение 2"],
  "tone": "описание тона видео",
  "pacing_notes": "заметки по темпу — где ускориться, где замедлиться",
  "climax_setup": "как основные блоки подводят к кульминации"
}}"""

CONTINUATION_PROMPT = """Продолжи сценарий. Вот что уже написано (блоки {start}-{end}):

{previous_blocks_summary}

Теперь напиши блоки с {next_start} до конца.
Продолжай с timestamp_start = "{timestamp}".

ВАЖНО:
- Сохраняй тот же тон, стиль и глубину раскрытия
- Не повторяй уже сказанное
- Каждый блок 5-10 развёрнутых talking_points
- В конце обязательно: кульминация + CTA + аутро

Ответ в JSON: {{ "blocks": [...], "key_messages": [...], "climax_setup": "..." }}"""


class ScriptStep(BaseStep):
    step_name = "script"

    def _build_cta_instructions(self) -> str:
        """Build CTA instructions from channel context."""
        ctx = self.get_channel_context()
        if not ctx:
            return ""

        lines = []
        cta = ctx.get("cta", {})

        lead = cta.get("lead_magnet", {})
        if lead.get("enabled") and lead.get("text"):
            placement = lead.get("placement", "intro")
            lines.append(
                f"- ЛИДМАГНИТ (вставить в {placement}): "
                f"Автор должен произнести: \"{lead['text']}\""
            )

        mid = cta.get("mid_roll", {})
        if mid.get("enabled") and mid.get("text"):
            block_num = mid.get("placement_after_block", 3)
            lines.append(
                f"- РЕКЛАМНАЯ ВСТАВКА (после блока {block_num}): "
                f"Автор должен произнести: \"{mid['text']}\""
            )

        end = cta.get("end_screen", {})
        if end.get("enabled") and end.get("text"):
            lines.append(
                f"- КОНЦОВКА: \"{end['text']}\""
            )

        if not lines:
            return ""
        return "\n\nCTA-вставки (ОБЯЗАТЕЛЬНО включить в сценарий):\n" + "\n".join(lines)

    def _load_expert_notes(self) -> str:
        """Load expert notes from project directory if exists."""
        notes_file = self.state.project_dir / "expert_notes.json"
        if notes_file.exists():
            try:
                data = json.loads(notes_file.read_text(encoding="utf-8"))
                notes = data.get("notes", [])
                if notes:
                    formatted = "\n".join(f"- {n}" for n in notes)
                    return (
                        f"\n\n=== ЭКСПЕРТНЫЕ ЗАМЕТКИ АВТОРА ===\n"
                        f"Автор добавил уникальные инсайты, которые ОБЯЗАТЕЛЬНО нужно вплести в сценарий:\n"
                        f"{formatted}\n"
                        f"ВАЖНО: эти заметки делают контент уникальным! "
                        f"Интегрируй их органично, используя стиль автора.\n"
                    )
            except Exception as e:
                logger.warning(f"Failed to load expert notes: {e}")
        return ""

    def execute(self) -> dict:
        content_plan = self.get_previous_step_data("content_plan")
        research = self.get_previous_step_data("research")
        sources = self.get_previous_step_data("sources")

        cta_instructions = self._build_cta_instructions()
        expert_notes = self._load_expert_notes()

        # Build sources context if available
        sources_context = ""
        if sources and not sources.get("skipped"):
            parts = []
            for f in sources.get("facts", []):
                parts.append(f"ФАКТ: {f.get('text', '')} (источник: {f.get('source', '?')})")
            for q in sources.get("quotes", []):
                parts.append(f"ЦИТАТА: \"{q.get('text', '')}\" — {q.get('author', '?')} ({q.get('source', '')})")
            for s in sources.get("statistics", []):
                parts.append(f"СТАТИСТИКА: {s.get('metric', '')} — {s.get('context', '')} (источник: {s.get('source', '?')})")
            for ins in sources.get("key_insights", []):
                parts.append(f"ИНСАЙТ: {ins}")
            if parts:
                sources_context = "\n\n=== РЕАЛЬНЫЕ ДАННЫЕ ИЗ ИСТОЧНИКОВ ===\n" + "\n".join(parts) + "\nВАЖНО: используй эти данные в сценарии! НЕ выдумывай факты — бери из источников.\n"

        # Duration: use recommended (now based on MAX competitor duration)
        rec_duration = int(research.get('_recommended_duration_minutes', content_plan.get('target_length_minutes', 12)))
        max_competitor = research.get('_max_duration_minutes', 0)
        best_dur = research.get('_best_video_duration_minutes', 0)

        logger.info(f"Target duration: {rec_duration} min (max competitor: {max_competitor} min, best video: {best_dur} min)")

        # Format system prompt with target duration
        system_prompt = SYSTEM_PROMPT.replace("{target_minutes}", str(rec_duration))

        # Build the main prompt
        base_prompt = f"""Напиши скелет сценария для YouTube-видео.

Заголовок: {content_plan.get('title', self.state.topic)}
Тема: {self.state.topic}

Контент-план:
- Хук: {content_plan.get('hook', '')}
- Структура: {json.dumps(content_plan.get('structure', []), ensure_ascii=False)}
- CTA: {content_plan.get('cta', '')}
- Retention-хуки: {json.dumps(content_plan.get('retention_hooks', []), ensure_ascii=False)}
- B-roll идеи: {json.dumps(content_plan.get('b_roll_ideas', []), ensure_ascii=False)}

Ключевые слова для SEO: {json.dumps(research.get('keywords', []), ensure_ascii=False)}

=== ДАННЫЕ О КОНКУРЕНТАХ ===
Средняя длительность конкурентов: {research.get('_avg_duration_minutes', 12)} мин
Максимальная длительность конкурента: {max_competitor} мин
Длительность лучшего видео: {best_dur} мин
НАША ЦЕЛЕВАЯ ДЛИТЕЛЬНОСТЬ: {rec_duration} минут — мы ДОЛЖНЫ быть не короче лучших конкурентов!
{sources_context}{expert_notes}
Требования:
- ЦЕЛЕВОЙ ХРОНОМЕТРАЖ: {rec_duration} минут — это критично! Сценарий должен быть РАЗВЁРНУТЫМ
- Каждый блок должен содержать 5-10 развёрнутых talking_points с фактами и примерами
- Обязательные блоки: хук (30 сек), интро, основные блоки, кульминация, CTA+аутро
- Тайминг (timestamp_start) и длительность (duration_seconds) для КАЖДОГО блока
- Ключевые переходы (transition_to_next) между блоками
- Retention-хук в каждом основном блоке
- Ключевая фраза (key_phrase) — дословно что произнести
- Визуальные указания для оператора/монтажёра
- Естественный, разговорный тон
- Кульминация должна быть логическим итогом основных блоков{cta_instructions}"""

        # For long videos (30+ min), split into multiple calls
        if rec_duration >= 30:
            result = self._generate_chunked(system_prompt, base_prompt, rec_duration, content_plan)
        else:
            result = self._generate_single(system_prompt, base_prompt)

        blocks_count = len(result.get("blocks", []))
        total_min = result.get("total_duration_minutes", "?")
        logger.info(f"Script skeleton created: {blocks_count} blocks, ~{total_min} min")

        # Validate required block types are present
        block_types = {b.get("block_type") for b in result.get("blocks", [])}
        required_types = {"hook", "main", "climax", "cta_outro"}
        missing = required_types - block_types
        if missing:
            logger.warning(f"Script skeleton missing block types: {missing}")

        return result

    def _generate_single(self, system_prompt: str, prompt: str) -> dict:
        """Generate script in a single Claude call."""
        response = self.ask_claude(system_prompt, prompt)
        return self._parse_response(response)

    def _generate_chunked(self, system_prompt: str, base_prompt: str, target_min: int, content_plan: dict) -> dict:
        """Generate long script in multiple Claude calls (30-min chunks)."""
        structure = content_plan.get("structure", [])
        chunk_size = 30  # minutes per chunk
        num_chunks = max(2, (target_min + chunk_size - 1) // chunk_size)

        logger.info(f"Long script ({target_min} min) — splitting into {num_chunks} chunks")

        # Divide structure blocks into chunks
        blocks_per_chunk = max(1, len(structure) // num_chunks)

        all_blocks = []
        timestamp_offset = 0

        for chunk_idx in range(num_chunks):
            is_first = chunk_idx == 0
            is_last = chunk_idx == num_chunks - 1

            chunk_start = chunk_idx * blocks_per_chunk
            chunk_end = len(structure) if is_last else (chunk_idx + 1) * blocks_per_chunk
            chunk_structure = structure[chunk_start:chunk_end]

            chunk_minutes = sum(b.get("duration_minutes", 5) for b in chunk_structure)

            if is_first:
                # First chunk: generate with hook + intro + first structure blocks
                chunk_prompt = base_prompt + f"""

ВНИМАНИЕ: Это ЧАСТЬ 1 из {num_chunks}. Напиши блоки для первых {chunk_minutes} минут:
- Хук + Интро + первые {len(chunk_structure)} основных блоков
- НЕ пиши кульминацию и CTA — они будут в последней части
- Закончи на transition_to_next для продолжения"""

                response = self.ask_claude(system_prompt, chunk_prompt)
                result = self._parse_response(response)
                all_blocks.extend(result.get("blocks", []))

                # Calculate timestamp offset
                for b in all_blocks:
                    timestamp_offset += b.get("duration_seconds", 120)
            else:
                # Continuation chunks
                prev_summary = "\n".join(
                    f"  Блок {b.get('block_number', '?')}: {b.get('name', '?')} ({b.get('duration_seconds', 0)}с)"
                    for b in all_blocks[-3:]  # Last 3 blocks for context
                )

                ts_min = timestamp_offset // 60
                ts_sec = timestamp_offset % 60
                timestamp_str = f"{ts_min}:{ts_sec:02d}"

                suffix = ""
                if is_last:
                    suffix = "\nЭто ПОСЛЕДНЯЯ часть — обязательно добавь КУЛЬМИНАЦИЮ и CTA+АУТРО!"

                chunk_prompt = CONTINUATION_PROMPT.format(
                    start=1,
                    end=len(all_blocks),
                    previous_blocks_summary=prev_summary,
                    next_start=len(all_blocks) + 1,
                    timestamp=timestamp_str,
                ) + f"""

Структура для этой части:
{json.dumps(chunk_structure, ensure_ascii=False)}

Целевая длительность этой части: ~{chunk_minutes} минут{suffix}"""

                response = self.ask_claude(system_prompt, chunk_prompt)
                continuation = self._parse_response(response)
                new_blocks = continuation.get("blocks", [])

                # Renumber blocks
                for b in new_blocks:
                    b["block_number"] = len(all_blocks) + 1
                    all_blocks.append(b)

                for b in new_blocks:
                    timestamp_offset += b.get("duration_seconds", 120)

                logger.info(f"Chunk {chunk_idx + 1}/{num_chunks}: +{len(new_blocks)} blocks, total {len(all_blocks)}")

        # Assemble final result
        final_result = result if 'result' in dir() else {}
        final_result["blocks"] = all_blocks
        final_result["total_duration_minutes"] = target_min
        final_result["_generation_chunks"] = num_chunks

        return final_result

    def _parse_response(self, response: str) -> dict:
        """Parse Claude response as JSON."""
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
            return {"raw_response": response}
