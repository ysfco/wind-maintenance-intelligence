# Wind Maintenance Intelligence (Türkçe özet)

Rüzgâr türbini bakım operasyonları için karar destek prototipi: **hangi türbin
önce, hangisi gerçekten planlanabilir, kalanlar neden backlog'da?**

Sistem bakım mühendisinin yerine karar vermez; teknik hasar durumunu, sözleşme
baskısını, müdahale süresini, hava pencerelerini ve ekip kapasitesini tek bir
karar zincirinde birleştirip gerekçesiyle birlikte sunar. Arıza tahmini yapmaz,
makine öğrenmesi içermez.

## Kurulum

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -e ".[dashboard,dev]"

windmaint run                     # 150 türbinlik filoyu skorla, planla, outputs/ klasörüne yaz
windmaint run --scheduler milp    # optimizasyonlu planlayıcı
windmaint compare                 # greedy ve MILP karşılaştırması
windmaint sensitivity             # sıralama ağırlıklara ne kadar duyarlı?
windmaint validate                # girdi dosyalarını denetle
windmaint --data data/sample run  # 5 türbinlik minimal vaka
streamlit run dashboard/app.py    # dashboard
pytest                            # testler
```

## Veri setleri

| Klasör | İçerik | Amaç |
|---|---|---|
| `data/fleet` | 150 türbin, 12 saha, 5 müşteri, 6 ekip, 126 hasar, 10 iş günü hava | Gerçekçi ölçek; varsayılan |
| `data/sample` | 5 türbin, 4 saha, 3 müşteri, 3 gün | Her sayı elle kontrol edilebilir |

İkisi de **sentetiktir**. Filo, tohumlu bir üreteçle oluşturulur
(`python scripts/generate_fleet.py`; tohum, başlangıç tarihi, gün sayısı ve
hasarlı türbin oranı parametredir). Saha kodları ve koordinatlar uydurmadır.

Kendi verinle: `windmaint --data klasor_yolu run`. Dosya şeması:
[docs/data-dictionary.md](docs/data-dictionary.md).

## Modüller

| Soru | Modül | Yöntem |
|---|---|---|
| Hangi türbin daha önemli? | `scoring` | Ağırlıklı çok kriterli skor; kritik türbinler her zaman önde |
| Skor neden böyle? | `scoring` | Üç ağırlıklı katkı ayrı kolon; toplamları skora tam eşit |
| Ne zaman yapılabilir? | `scheduling`, `optimizer` | Hava, müşteri availability limiti, ekip kapasitesi; greedy ve MILP |
| Neden planlanamadı? | `backlog` | Her slot her kısıta karşı denetlenir; ana engel ve "kıl payı" slot |
| Ne yapmalıyım? | `recommendations` | Plandan üretilen kural tabanlı açıklama |
| Sıralamaya güvenebilir miyim? | `sensitivity` | Ağırlık belirsizliği altında sıra kararlılığı |

## Filo verisinin gösterdikleri

150 türbinin 64'ünde açık hasar kaydı var (18'i kritik); iki iş haftası planlanıyor.

| Planlayıcı | Planlanan | Backlog | Kritik backlog | Geciken | Plan maliyeti |
|---|---|---|---|---|---|
| Greedy | 38 / 64 | 26 | 3 | 1 | 337,0 |
| MILP | 38 / 64 | 26 | 2 | 0 | 295,7 |

* **Darboğaz ekip değil, sözleşme limiti.** Backlog'daki 26 türbinin 19'unda
  ana engel availability limiti; ekipler %63 dolu.
* **MILP daha fazla iş değil, doğru işleri planlıyor.** Planlanabilir bütün
  kritik türbinler süresinde yerleşiyor; greedy birini backlog'da, birini
  gecikmeli bırakıyor.
* **İki kritik türbin hiçbir planla planlanamıyor:** müşterinin bütçesi sıfır.
  Bu bir algoritma değil, müşteri kararı konusu.
* **Sıra numarası değil, bant okunmalı:** tam sıralama ağırlıklara çok duyarlı,
  ilk 10 ise ortalama %94 oranında aynı kalıyor.

## Minimal vakanın gösterdikleri

* **Öncelik ≠ plan.** En yüksek öncelikli T5 hiç planlanamıyor: müşterinin
  availability bütçesi sıfır (80 türbin × %2 = 1,6 → 1 türbin; 1'i zaten duruyor).
* **Backlog'un nedeni türbine göre değişiyor.** T4 tamamen havaya takılıyor
  (yapılacak bir şey yok); T1 ve T5 ise hava uygunken yalnızca sözleşme
  limitine takılıyor (müdahale edilebilir).
* **Greedy planlayıcı fırsat kaçırıyor.** Greedy 5 işten 2'sini, MILP 3'ünü
  planlıyor: T2 bir gün kaydırılınca T1 tek uygun gününe sığıyor.

## Prototipe göre ne değişti?

* Tek dosyalık `main.py` ve `dashboard.py`, test edilebilir bir pakete
  (`src/windmaint`) ve ince bir arayüze (`dashboard/`) ayrıldı.
* Dashboard artık Excel okumuyor; motoru canlı çalıştırıyor. Yan paneldeki her
  kontrol bir senaryo.
* "AI Recommendations" sayfasındaki `skor × 0.55 / 0.25 / 0.20` dökümü ve
  "Weather Risk" kaldırıldı; yerine gerçek katkı kolonları geldi.
* `Failure_Probability = skor × 100` kolonu kaldırıldı (olasılık değildi).
* Site adına göre yazılmış sabit metinler (`site in ["C", "D"]`) kaldırıldı.
* Min–max yerine sabit referanslı normalizasyon varsayılan oldu; eskisi
  seçenek olarak duruyor ve eski sonuçları birebir üretiyor (testle doğrulanıyor).
* Kısıt denetimi artık ilk hatada durmuyor; bir slotu aynı anda engelleyen
  bütün kısıtlar kaydediliyor.
* Ekip modeli kaynak havuzuna genellendi (tip, taşeron, çoklu bölge).
* MILP planlayıcı, duyarlılık analizi, veri doğrulama, Open-Meteo istemcisi,
  testler ve CI eklendi.
* 150 türbinlik filo verisi ve üreteci eklendi; arayüz bu ölçeğe göre
  düzenlendi (müşteri/bölge/saha/kategori filtreleri, türbin detay sayfası,
  ekip sevk tablosu, saha bazlı özetler).

Tasarım eleştirisi ve pilot yol haritası: [docs/design-review.tr.md](docs/design-review.tr.md).
