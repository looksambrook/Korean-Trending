# MemeInterpret: Towards an All-in-One Dataset for Meme Understanding

[![Paper](https://img.shields.io/badge/EMNLP%20Findings-2025-red)](https://aclanthology.org/2025.findings-emnlp.871/)
[![PDF](https://img.shields.io/badge/PDF-ACL%20Anthology-blue)](https://aclanthology.org/2025.findings-emnlp.871.pdf)
[![DOI](https://img.shields.io/badge/DOI-10.18653%2Fv1%2F2025.findings--emnlp.871-orange)](https://doi.org/10.18653/v1/2025.findings-emnlp.871)
[![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey)](https://creativecommons.org/licenses/by/4.0/)

Official data and code repository for the paper:

> **MemeInterpret: Towards an All-in-One Dataset for Meme Understanding**
> Jeongsik Park, Khoi P. N. Nguyen, Jihyung Park, Minseok Kim, Jaeheon Lee, Jae Won Choi, Kalyani Ganta, Phalgun Ashrit Kasu, Rohan Sarakinti, Sanjana Vipperla, Sai Sathanapalli, Nishan Vaghani, Vincent Ng.
> _Findings of the Association for Computational Linguistics: EMNLP 2025_, pp. 16073–16087, Suzhou, China.

📄 **Paper:** https://aclanthology.org/2025.findings-emnlp.871/
🔗 **Project repo:** https://github.com/npnkhoi/MemeInterpret

---

## 🧠 What is MemeInterpret?

**MemeInterpret** is a large, human-annotated corpus that unifies the three major categories of **Computational Meme Understanding (CMU)** tasks — **classification**, **explanation**, and **captioning** — into a single all-in-one resource.

Built strategically on top of the [Facebook Hateful Memes (FHM) dataset](https://ai.meta.com/tools/hatefulmemes/), MemeInterpret contributes the missing piece for meme captioning research by providing, for **each meme**:

- 🖼️ **Surface message** — a literal description of what the meme depicts (image + overlaid text).
- 📚 **Background knowledge** — the world / cultural / political knowledge required to interpret the meme.
- 💬 **Meme caption(s)** — a concise sentence stating the underlying meaning the author wants to convey.
- 🏷️ **Hatefulness label** — inherited from the FHM dataset (hateful / non-hateful).
- 🤖 **Auto image caption** — a machine-generated caption useful as a baseline visual signal.

This decomposition makes meme captioning **interpretable** (surface + knowledge → caption) and lets researchers study how meme captioning relates to other CMU tasks such as hate-speech classification and hate-explanation generation.

### Why use MemeInterpret?

- ✅ **First all-in-one CMU corpus** — connects classification, explanation, and captioning under one schema.
- ✅ **Subtask decomposition** — explicit _surface message_ and _background knowledge_ annotations enable controllable / interpretable meme captioning research.
- ✅ **Aligned with FHM and HatReD** — directly compatible with widely-used meme classification and explanation benchmarks.
- ✅ **Ready for VLMs / LLMs** — clean JSON, prompt-friendly fields, and reference fine-tuning / evaluation code for LLaVA-class models.

---

## 📦 Dataset

The annotations live under [data/memeinterpret/](data/memeinterpret/):

| Split | File                                                                     | # Memes |
| ----: | ------------------------------------------------------------------------ | ------: |
| Train | [data/memeinterpret/train_data.json](data/memeinterpret/train_data.json) |   4,648 |
|   Dev | [data/memeinterpret/dev_data.json](data/memeinterpret/dev_data.json)     |   1,162 |
|  Test | [data/memeinterpret/test_data.json](data/memeinterpret/test_data.json)   |   1,000 |

### Schema

Each entry is a JSON object with the following fields:

| Field                       | Type        | Description                                                              |
| --------------------------- | ----------- | ------------------------------------------------------------------------ |
| `img_path`                  | `str`       | Relative path to the meme image (FHM filename).                          |
| `text`                      | `str`       | OCR'd / overlaid text of the meme (from FHM).                            |
| `label`                     | `int`       | Hatefulness label inherited from FHM (`0` = non-hateful, `1` = hateful). |
| `auto_image_caption`        | `str`       | Automatically generated caption of the meme image.                       |
| `image_caption`             | `str`       | Human-written description of the visual content.                         |
| `surface_message`           | `str`       | Human-written literal description combining visual + textual content.    |
| `background_knowledge_list` | `list[str]` | World knowledge required to interpret the meme.                          |
| `meme_caption_list`         | `list[str]` | One or more reference meme captions (the meaning the author conveys).    |

### Example

```json
{
  "img_path": "fhm_img/original/74059.png",
  "text": "when you try to catch a dark type pokemon",
  "label": 0,
  "auto_image_caption": "3 yellow Pokemon characters are standing in a field at night.",
  "image_caption": "It is an image of 3 Pikachus standing next to a tree in the dark.",
  "surface_message": "It is an image of 3 Pikachus standing next to a tree in the dark. The author describes this image as: 'when you try to catch a dark type pokemon'",
  "background_knowledge_list": [
    "1. Pikachu is a Pokemon.",
    "2. Dark type Pokemon are easier to catch during the dark."
  ],
  "meme_caption_list": ["Dark-type Pokemon are best caught at night."]
}
```

### Getting the meme images

Due to licensing, **meme images are NOT redistributed in this repository.** Download them from the official source:

1. Get the [Facebook Hateful Memes Challenge](https://ai.meta.com/tools/hatefulmemes/) dataset.
2. Place the images under:
   ```
   fhm_img/original/<id>.png
   ```
   so that the `img_path` field in each JSON entry resolves correctly.

> ⚠️ If you cannot access the official release, an _unofficial_ mirror exists on [HuggingFace](https://huggingface.co/datasets/neuralcatcher/hateful_memes/tree/main/img). Use at your own risk and check the license.

---

## 🧩 Supported tasks

MemeInterpret natively supports — and was designed to **bridge** — these CMU tasks:

| Category       | Task                                            | Inputs       | Targets                     |
| -------------- | ----------------------------------------------- | ------------ | --------------------------- |
| Classification | Hateful meme detection                          | image + text | `label`                     |
| Explanation    | Hate-explanation generation (HatReD-compatible) | image + text | external HatReD reasonings  |
| Captioning     | **Meme captioning (main task)**                 | image + text | `meme_caption_list`         |
| Subtask        | **Surface message generation**                  | image + text | `surface_message`           |
| Subtask        | **Background knowledge generation**             | image + text | `background_knowledge_list` |

The paper shows that explicitly modeling the two subtasks (surface message + background knowledge) substantially improves meme captioning, and that captioning is in turn predictive of hatefulness classification and explanation quality.

---

## ⚙️ Installation

```bash
pip install poetry
poetry shell
poetry install

# Evaluation utilities (BERTScore / BLEU / ROUGE / SelfCheck-NLI)
git clone https://github.com/chjwon/LLM_EVAL
```

Python 3.12 is recommended; see [pyproject.toml](pyproject.toml) for the full pinned dependency stack (PyTorch 2.5 + CUDA 11.8, Transformers < 4.46, PEFT < 0.13, bitsandbytes, etc.).

---

## 🚀 Code

| File                               | Purpose                                                                      |
| ---------------------------------- | ---------------------------------------------------------------------------- |
| [src/data_fhm.py](src/data_fhm.py) | Dataset loaders for MemeInterpret + FHM-aligned splits.                      |
| [src/finetune.py](src/finetune.py) | LoRA fine-tuning loop for LLaVA-style VLMs on any prompt template.           |
| [src/evaluate.py](src/evaluate.py) | Inference + automatic evaluation (BLEU / ROUGE / BERTScore / SelfCheck-NLI). |
| [src/utils.py](src/utils.py)       | Prompt registry (`PromptId`), collators, and metric helpers.                 |

Example fine-tuning command (from [src/finetune.py](src/finetune.py)):

```bash
CUDA_VISIBLE_DEVICES=0,1 python -m src.finetune \
    --model_name llava-hf/llava-1.5-7b-hf \
    --num_epochs 3 \
    --train_batch_size 2 \
    --eval_batch_size 2 \
    --logging_steps 50 \
    --eval_freq 700 \
    --max_new_tokens 100 \
    --prompt_id MC/autoic_text \
    --output_dir weights/1011_autoic_text
```

---

## 📜 License

- **Annotations** in this repository are released under the [Creative Commons Attribution 4.0 International License (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).
- **Meme images** are _not_ included; they remain under the license of the [Facebook Hateful Memes Challenge](https://ai.meta.com/tools/hatefulmemes/).

---

## 📝 Citation

If you use MemeInterpret in your research, please cite:

```bibtex
@inproceedings{park-etal-2025-memeinterpret,
    title     = "{M}eme{I}nterpret: Towards an All-in-One Dataset for Meme Understanding",
    author    = "Park, Jeongsik and Nguyen, Khoi P. N. and Park, Jihyung and Kim, Minseok and Lee, Jaeheon and Choi, Jae Won and Ganta, Kalyani and Kasu, Phalgun Ashrit and Sarakinti, Rohan and Vipperla, Sanjana and Sathanapalli, Sai and Vaghani, Nishan and Ng, Vincent",
    booktitle = "Findings of the Association for Computational Linguistics: EMNLP 2025",
    year      = "2025",
    address   = "Suzhou, China",
    publisher = "Association for Computational Linguistics",
    pages     = "16073--16087",
    doi       = "10.18653/v1/2025.findings-emnlp.871",
    url       = "https://aclanthology.org/2025.findings-emnlp.871/"
}
```

---

## 🙏 Acknowledgements

MemeInterpret is built on top of the [Facebook Hateful Memes Challenge](https://ai.meta.com/tools/hatefulmemes/) and is designed to be compatible with the [HatReD](https://github.com/Social-AI-Studio/HatReD) explanation corpus. We thank the authors of these datasets for making the underlying memes and hate-explanation annotations available to the community.

---

## 🔎 Keywords

_meme understanding · meme captioning · hateful memes · multimodal NLP · vision-language models · VLM · LLaVA · computational meme understanding (CMU) · hate-speech explanation · multimodal dataset · EMNLP 2025_
