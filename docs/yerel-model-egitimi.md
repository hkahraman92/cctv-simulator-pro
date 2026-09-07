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

## 2. LoRA / QLoRA fine-tune — `scripts/finetune_compliance.py`

Tek 16–24 GB GPU (RTX 3090/4090) veya kiralık A100 birkaç saat (~$5–20).

```bash
# GPU'suz: yalnız veriyi hazırla + train/eval böl
py -3.13 scripts/finetune_compliance.py --prepare-only

# tam eğitim (unsloth kurulu, GPU var)
pip install "unsloth @ git+https://github.com/unslothai/unsloth.git" trl datasets
py -3.13 scripts/finetune_compliance.py --out out/cctv-uygunluk
```

Boru hattı: `training_log.build_instruction_dataset` → `split_dataset` (spec
hash'ine göre, bir şartname hiç iki tarafa düşmez) → QLoRA SFT (r=32) → q4_k_m
GGUF + Ollama `Modelfile`. Çıktı dizininde `train.jsonl`, `eval.jsonl`,
`<ad>.Q4_K_M.gguf`, `Modelfile`.

```
ollama create cctv-uygunluk -f out/cctv-uygunluk/Modelfile
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

`scripts/eval_compliance.py` — bir gold JSONL'e karşı model koşturur ve metrik
basar (JSON parse oranı, DORI ister precision/recall/F1, matris durum doğruluğu).
Metrik çekirdeği `cctv_simulator.compliance_eval` (GPU/ağ yok, birim testli).

```bash
py -3.13 scripts/eval_compliance.py out/cctv-uygunluk/eval.jsonl --model rule          # offline taban
py -3.13 scripts/eval_compliance.py out/cctv-uygunluk/eval.jsonl --model cctv-uygunluk --baseline qwen2.5:7b
```

`training_log.split_dataset(recs, eval_ratio)` test bölümünü ayırır (spec hash'ine
göre — bir şartname hiç iki tarafa düşmez).

## 5. Sürekli döngü

Haftalık: biriken override'larla LoRA'yı yeniden eğit. Model tam kullanıcıların
takıldığı hataları öğrenir.
