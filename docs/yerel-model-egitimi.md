# Yerel şartname-inceleme modeli: geliştirme ve eğitim

Görev: **Türkçe CCTV teknik şartnamesi → yapılandırılmış JSON** (ister listesi +
uygunluk matrisi). Fizik/kural motoru deterministik kalır; model yalnız
"belirsiz dil → yapı" kısmını yapar.

## 0. Eğitimsiz — önce bunlar

| Adım | Yapıldı mı |
|---|---|
| Model: `qwen2.5:7b` (TR + JSON güçlü). `ollama pull qwen2.5:7b` | ✅ varsayılan |
| `num_ctx=16384` (uzun şartname kesilmesin) | ✅ `analyze_with_ollama` |
| Few-shot örnek + `dori` talimatı prompt'ta | ✅ `build_compliance_prompt` |
| `format: json` zorlanmış çözümleme | ✅ |
| RAG: EN 62676-4 madde metni + geçmiş şartnameler bağlama | ⏳ (öneri) |

RAG için: `nomic-embed-text` (veya çok dilli `bge-m3`) via Ollama + sqlite/numpy
vektör deposu; şartname başına en ilgili 5 madde/örnek prompt'a.

## 1. Veri toplama

- `spec_assistant` her analizi + her **override**'ı loglar
  (`%APPDATA%\<uygulama>\training\compliance.jsonl`).
- Override UI = etiketleme hattı: kullanıcı bir kararı düzelttiğinde altın etiket
  oluşur.
- Kamu ihaleleri (EKAP) açık — 50–500 gerçek şartname toplayıp Gemini/GPT-4 ile
  ön-etiketle (öğretmen-öğrenci distilasyonu), sonra elle düzelt.
- Tezgâhta **🧠 Eğitim Verisi Dışa Aktar** → `cctv_compliance_train.jsonl`
  (`{"messages": [system, user, assistant]}`, override'lar assistant hedefine
  katılmış).

Anlamlı fine-tune için ~**300+** düzeltilmiş örnek.

## 2. LoRA / QLoRA fine-tune (unsloth)

Tek 16–24 GB GPU (RTX 3090/4090) veya kiralık A100 birkaç saat (~$5–20).

```python
# pip install unsloth
from unsloth import FastLanguageModel
import json

model, tok = FastLanguageModel.from_pretrained(
    "unsloth/Qwen2.5-7B-Instruct-bnb-4bit", max_seq_length=16384, load_in_4bit=True)
model = FastLanguageModel.get_peft_model(model, r=32, lora_alpha=32,
    target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"])

rows = [json.loads(l) for l in open("cctv_compliance_train.jsonl", encoding="utf-8")]
def fmt(ex):
    return {"text": tok.apply_chat_template(ex["messages"], tokenize=False)}
from datasets import Dataset
ds = Dataset.from_list([fmt(r) for r in rows])

from trl import SFTTrainer, SFTConfig
SFTTrainer(model=model, tokenizer=tok, train_dataset=ds,
    args=SFTConfig(per_device_train_batch_size=1, gradient_accumulation_steps=8,
        num_train_epochs=3, learning_rate=2e-4, warmup_ratio=0.05,
        logging_steps=5, output_dir="out", optim="adamw_8bit")).train()

model.save_pretrained_gguf("cctv-uygunluk", tok, quantization_method="q4_k_m")
```

Sonra:

```
ollama create cctv-uygunluk -f Modelfile   # FROM ./cctv-uygunluk.Q4_K_M.gguf
```

`analyze_with_ollama(model="cctv-uygunluk")` — küçük/hızlı, senin formatında.

## 3. Görev ayrıştırma (en güvenilir, GPU'suz)

1. cümle/madde bölme — kurallı (kısmen var).
2. madde sınıflandırma (ister mi? kategori?) — CPU'da çalışan küçük Türkçe BERT
   (`dbmdz/bert-base-turkish-cased`) fine-tune, `transformers` + `Trainer`.
3. sayısal çıkarım — regex + zor durumlar için küçük model.
4. skorlama — `compliance_optics` + `evaluate_rule_requirement` (LLM'e aritmetik
   YOK).

## 4. Değerlendirme (her değişiklikten önce)

```python
# JSON parse oranı, kural motorunun bulduğu DORI isterlerini bulma F1'i,
# matris status doğruluğu (insan gold'a karşı)
```

`training_log.read_all()` ile analiz kayıtlarını çekip test bölümü ayır.

## 5. Sürekli döngü

Haftalık: biriken override'larla LoRA'yı yeniden eğit. Model tam kullanıcıların
takıldığı hataları öğrenir.
