# Tasarım incelemesi

Bu belge, prototipin teknik aktarım dokümanındaki 16 soruya eleştirel cevap
verir. Her başlıkta önce tespit, sonra bu repoda ne yapıldığı, sonra açık kalan
iş yer alır. Sayılar bu repodaki kodla üretilmiştir: aksi belirtilmedikçe 5
türbinlik `data/sample`, "filo" denildiğinde 150 türbinlik sentetik `data/fleet`.

## Özet: en önemli beş bulgu

1. **En yüksek öncelikli türbin hiçbir planla planlanamıyor.** T5'in müşterisinde
   availability bütçesi sıfır: `floor(80 × %2) = 1` türbin durabilir, 1'i zaten
   duruyor. Sözleşme aynı türbinin önceliğini artırıyor (soft) ve planlanmasını
   yasaklıyor (hard). İki etki birbirine karşı çalışıyor.
2. **Eski kök neden analizi bunu gizliyordu.** Kısıtlar sırayla denetlenip ilk
   hatada durulduğu için T5'te 6 slotun 5'i "hava" olarak kaydediliyor, ana
   engel "Weather" görünüyordu. Tüm kısıtlar denetlenince sözleşme limiti 6
   slotun 6'sını engelliyor.
3. **Greedy planlayıcı 5 türbinlik veride bile optimumu kaçırıyor.** 2 iş yerine
   3 iş planlanabiliyor (T2 bir gün kayınca T1 tek uygun gününe sığıyor).
4. **Min–max normalizasyon skoru filoya bağımlı kılıyor.** T5 veriden çıkınca
   T2'nin skoru 0,69'dan 0,91'e, T4'ünki 0,50'den 0,71'e çıkıyor; türbinlerde
   hiçbir şey değişmediği hâlde T4 "kritik risk" bandına giriyor.
5. **Due skoru bağımsız bir kriter değil.** `Recommended_Due_Days` yalnızca
   kategori ve artan hasar sayısından türetiliyor; ikisi de teknik skorun
   girdisi. Fiilen teknik ağırlık %60 değil, %75'e yakın.

## Filo ölçeğinde (150 türbin) ek bulgular

`data/fleet`: 12 saha, 5 müşteri, 6 ekip, 64 iş (18'i kritik), 10 iş günü.

1. **Darboğaz ekip değil, sözleşme limiti.** 26 backlog türbininin 19'unda ana
   engel availability limiti, 7'sinde hava. Ekiplerin doluluk oranı %63.
   "Gün başına türbin" varsayımı (madde 6) planın büyük kısmını tek başına
   belirliyor; bu varsayım doğrulanmadan sonuçlara güvenilmemeli.
2. **MILP daha fazla iş değil, doğru işleri planlıyor.** İki yöntem de 38 iş
   planlıyor (limitler geçirgenliği sınırlıyor). Greedy planlanabilir bir kritik
   türbini backlog'da bırakıyor, bir diğerini süresinden 4 gün sonraya koyuyor.
   MILP 8 işin yerini değiştirip planlanabilir bütün kritik türbinleri
   süresinde yerleştiriyor.
3. **Sıfır bütçeli müşteri ölçekte de çıkıyor.** 12 türbinlik müşteride %97
   hedef `floor(0,36) = 0` bütçe demek: iki kritik türbin hiçbir planla
   planlanamıyor ve o bölgenin ekibi boşta kalıyor. Küçük portföylerde türbin
   sayısına dayalı limit yapısal olarak sıfıra yuvarlanır.
4. **Sıra numarası değil, bant okunmalı.** Ağırlıklar oynatıldığında 64
   türbinin tam sıralaması pratikte hiç tekrarlanmıyor; ilk 3 denemelerin
   %29'unda aynı kalıyor. Buna karşılık ilk 10'un ortalama %94'ü ilk 10'da
   kalıyor ve bir türbin ortalama 1,5 sıra oynuyor.

---

## 1. Technical Score mühendislik açısından savunulabilir mi?

Sıralama sezgiseli olarak evet; ölçüm olarak hayır. Zayıf noktalar:

* **Toplamsal ve sınırsız.** Çok sayıda küçük hasar tek bir ağır hasarı
  geçebilir: 12 adet sabit severity-2 hasar `2·2 + 12 = 16` puan alır; tek bir
  yeni severity-5 hasar `10 + 1,5 + 1 + 3 = 15,5`. Hangisinin önce gelmesi
  gerektiği açık.
* **Severity sıralı (ordinal) bir ölçek**, formül onu aralıklı ölçek gibi
  kullanıyor: 4→5 ile 1→2 aynı ağırlıkta.
* **Hasarın türü ve yeri yok.** Kanat kökündeki çatlak ile uç bölgesindeki
  yüzey erozyonu aynı severity ile aynı puanı alır.
* **Zaman yok.** `Increasing_Count` iki denetim arasındaki süreyi bilmiyor;
  3 ayda 1→2 ile 3 yılda 1→2 aynı sayılıyor. Anlamlı büyüklük artış *hızıdır*.
* `Delta_Sum` hesaplanıyor ama skorda kullanılmıyor.

**Yapılan:** katsayılar ve kritik eşiği `config/default.yaml`'a taşındı; kritik
türbinlerin her zaman önde olduğu katmanlı sıralama eklendi
(`ranking.critical_first`). Böylece formül yalnızca katman içi sıralamayı
belirliyor ve yukarıdaki "çok küçük hasar > tek ağır hasar" sorunu kritik/kritik
olmayan sınırında ortadan kalkıyor.

**Açık:** hasar sayısını doyuran bir terim (ör. `log(1+n)`), hasar tipi
ağırlıkları, denetim tarihlerinden artış hızı.

## 2. Çifte sayım var mı?

Var, üç katmanda:

* Tek bir severity-5 hasar `Max_Severity` (×2), `Total_Damage_Count` (×1) ve
  `Critical_Count` (×3) üzerinden üç kez; artıyorsa `Increasing_Count` ile
  dördüncü kez sayılıyor.
* Aynı hasar `Category = Critical` yaparak `Recommended_Due_Days`'i kısaltıyor
  ve %15 ağırlıklı due bileşenine de giriyor.
* `Max_Severity ≥ 4` ile `Critical_Count ≥ 1` aynı bilgidir.

Çifte sayım tek başına hata değildir; kritik hasarı bilerek öne çekmenin bir
yoludur. Sorun, bunun örtük olması ve "teknik %60, due %15" ifadesinin gerçeği
yansıtmamasıdır. Due bileşeni ancak gerçek tarihe dayanırsa yeni bilgi taşır:
`kalan gün = denetim tarihi + izin verilen gün − bugün`. O zaman "30 günlük
işin 28. günü" ile "5 günlük işin 1. günü" ayrışır.

## 3. Min–max küçük veri setinde sıralamayı fazla hassaslaştırıyor mu?

Evet, ve sorun hassasiyetten büyük:

* En düşük türbin her bileşende tam 0 alır. T1'in severity-3 hasarı var ama
  teknik katkısı 0; skoru (0,25) tamamen sözleşmeden geliyor.
* En yüksek türbin her zaman 1 alır. Filo ne kadar sağlıklı olursa olsun biri
  hep "kırmızı" görünür; sabit 0,70 eşiği bu yüzden anlamını yitirir.
* Skor diğer türbinlere bağlıdır (özet, madde 4). Haftadan haftaya
  karşılaştırma yapılamaz.

**Yapılan:** varsayılan normalizasyon sabit referanslı oldu. Teknik skor
`technical_reference_max = 25`'e bölünür (severity 5, dört hasar, üçü artıyor,
ikisi kritik: 24,5); sözleşme skoru olası en büyük değere (`2 × boost_max`);
due skoru kural tablosundaki en uzun ve en kısa süreye göre ölçeklenir.
`tests/test_scoring.py` bir türbin çıkarıldığında diğer skorların değişmediğini
doğrular. Eski davranış `method: minmax` ile duruyor ve eski sayıları birebir
üretiyor.

Dikkat: referanslı ölçekte T4 (kritik, 0,534) ile T3 (kritik değil, sözleşme
destekli, 0,527) neredeyse eşit. Kritik türbini önde tutan şey skor değil,
katmanlı sıralama.

## 4. 0,60 / 0,25 / 0,15 nasıl kalibre edilmeli?

Şu an kalibre değil; savunulabilir tek ifade "mühendislik sezgisi"dir. Sırayla:

1. **Önce duyarlılığı ölç** (yapıldı: `windmaint sensitivity`). Ağırlıklar
   mevcut değerlerin çevresinde rastgele oynatıldığında tam sıralama 1000
   denemenin %83'ünde aynı kalıyor; değişenlerin çoğunda T5 ile T2 yer
   değiştiriyor. Filoda tablo farklı: tam sıra hiç korunmuyor ama ilk 10'un
   %94'ü yerinde kalıyor. Yani ağırlıklar "kim ilk 10'da" sorusunu değil,
   "6. mı 7. mi" sorusunu belirliyor; ikincisini tartışmanın getirisi azdır.
2. **Uzman sıralamasından öğren.** 20–30 gerçek vakayı 3–5 servis mühendisine
   bağımsız sıralat. Ağırlıkları, model sıralaması ile uzman sıralaması
   arasındaki Kendall τ'yu en büyük yapacak şekilde seç. Bu aynı zamanda
   mühendislerin kendi aralarında ne kadar anlaştığını gösterir; anlaşamıyorlarsa
   model de anlaşamaz.
3. **İkili karşılaştırma (AHP)** daha ucuz bir alternatif: "teknik durum
   sözleşmeden kaç kat önemli?" sorularından ağırlık çıkar.
4. **Uzun vadede ağırlığı parayla değiştir.** Gecikme maliyeti (üretim kaybı +
   ceza + hasar büyüme maliyeti) hesaplanabildiğinde ağırlığa gerek kalmaz.
   Veri yokken bunu taklit etmemek doğru karar.

## 5. Contract Time Factor doğru tanımlanmış mı?

"Sözleşme döneminin geçen oranı" olarak doğru; "sözleşme baskısı" olarak eksik.
Baskı iki şeye bağlıdır: hedefe uzaklık ve kalan süre. Daha anlamlı büyüklük,
hedefi tutturmak için kalan dönemde gereken availability'dir:

```
gereken = (hedef × toplam_ay − gerçekleşen × geçen_ay) / kalan_ay
```

`gereken > %100` ise hedef artık tutturulamaz; bu ayrı bir rejimdir ve öncelik
artırmak yerine hasar sınırlaması gerekir. `gereken ≤ hedef` ise baskı yoktur.

Mevcut formülde bu ayrımı elle girilen `Priority_Boost` yapıyor ("hedef
altındaysa 2"). Oysa aynı tabloda `Current_Availability` ve
`Contract_Target_Availability` zaten var; boost onlardan hesaplanmalı, elle
girilmemeli. Ayrıca ay bilgisi müşteri düzeyinde, availability site düzeyinde;
sözleşmenin hangisi üzerinden ölçüldüğü netleşmeli.

**Yapılan:** faktör 0–1 aralığına kırpılıyor, arayüzde "Contract period
elapsed" olarak adlandırılıyor ve ceza tahmini olmadığı yazıyor.

## 6. Soft baskı ve hard limit birlikte nasıl modellenmeli?

Mevcut model üç noktada kırılgan:

* **İki etki çatışıyor** (özet, madde 1). Hedefin altındaki müşteri hem öncelik
  alıyor hem de bütçesi olmadığı için planlanamıyor.
* **Birim yanlış.** Limit "gün başına türbin" sayıyor. Availability ise
  zaman tabanlıdır: 4 saatlik iş ile tam gün duruş aynı sayılmamalı. Doğru
  birim türbin-saattir: `bütçe = (1 − hedef) × N × dönem_saati − harcanan`.
  Her iş süresi kadar bütçe tüketir; kısıt gün gün değil, dönem üzerinden yazılır.
* **Planlı bakım ile arıza duruşu ayrılmıyor.** Birçok servis sözleşmesi planlı
  bakımı availability hesabında farklı ele alır. Bu, sözleşme metninden teyit
  edilmeden modele gömülmemeli; şu an en zayıf varsayım budur.

Ayrıca kritik hasarın onarımını limit yüzünden ertelemek, daha uzun plansız
duruş riskini büyütür. Bu yüzden limit kritik işler için "aşılabilir ama
maliyetli" olmalı.

**Yapılan:** `contracts.critical_override` seçeneği. Açıldığında severity-5
türbinler limite takılmaz, çıktıda `Contract_Override` ile işaretlenir ve öneri
metni müşteri onayı ister. Varsayılan kapalı. Bütçe hesabında `int()` yerine
kayan nokta hatasına karşı korumalı `floor` kullanılıyor.

**Açık:** türbin-saat bütçesi, iş süreleri, MILP'te limit aşımı için ceza terimi.

## 7. Hava skora mı girmeli, yalnızca kısıt mı olmalı?

Yalnızca kısıt. Skor "bu türbin ne kadar önemli?" sorusunu cevaplar; hava
türbinin değil, günün özelliğidir. Skora girerse rüzgârlı sahadaki türbinler
teknik durumlarından bağımsız olarak "riskli" görünür ve skor zamanla
karşılaştırılamaz hâle gelir.

Havanın önceliği etkilemesi gereken tek meşru durum pencere kıtlığıdır:
önümüzdeki 10 günde tek uygun pencere varsa onu kaçırmamak önemlidir. Bunu
skor değil, ileriye bakan planlayıcı çözer. MILP'in T1'i tek uygun gününe
yerleştirmesi tam olarak budur.

Veriyle ilgili not: `weather.csv`'deki 24 slotun 12'sinde elle girilmiş
`Workable` bayrağı rüzgâr hızıyla çelişiyor (ör. 7 m/s "No", 13 m/s "Yes").
`windmaint validate` bunu raporlar; `workability_source: rules` ile bayrak
yerine limitler kullanılabilir. Limitler iş tipine göre ayrılmalı (kanat
denetimi, halatlı erişim, vinç).

## 8. Greedy hangi koşullarda ciddi şekilde yanılır?

Ortak bir kapasiteyi paylaşan işlerin uygun pencereleri farklı olduğunda.
Mekanizma: yüksek öncelikli iş, başka seçeneği olduğu hâlde, düşük öncelikli
işin *tek* seçeneği olan slotu alır.

* **Örnek veri:** müşteri limiti günde 1. T2 dört pencerede çalışabiliyor, T1
  yalnızca 15.04 sabahı. Greedy 15.04'ü T2'ye veriyor; T1 backlog'a düşüyor.
* **Rastgele 30 türbinlik filolar** (8 tohum, `tests/conftest.py`): MILP üç
  tohumda 1–2 iş fazla planlıyor; kritik backlog üç tohumda azalıyor (birinde
  8'den 5'e). Kalan beş tohumda iki yöntem aynı sayıda iş planlıyor.
* **Filo verisi:** iki yöntem aynı sayıda iş planlıyor ama greedy bir kritik
  türbini backlog'da, birini gecikmeli bırakıyor (yukarıdaki ek bulgular, 2).
  Greedy'nin kaybı her zaman iş sayısında görünmez; hangi işin planlandığına
  ve ne zaman planlandığına da bakmak gerekir.
* **Tek tip kapasite tek darboğazsa** (çok iş, az ekip, başka kısıt yok) iki
  yöntem aynı planı verir; greedy'nin kaybettiği bir şey yoktur.

Greedy'nin ikinci zaafı "ilk uygun slot" kuralıdır: gelecekteki işleri hiç
görmediği için takvimi baştan doldurur. Açıklanabilirliği ise gerçek bir
avantajdır; bu yüzden varsayılan olarak kaldı ve her iki yöntem aynı maliyet
fonksiyonuyla puanlanıyor.

## 9. MILP amaç fonksiyonu nasıl kurulmalı?

Uygulanan model (`optimizer.py`):

```
x[j,s,r] ∈ {0,1}        j: iş, s: slot, r: kaynak (yalnızca hava uygunsa ve kaynak bölgeyi kapsıyorsa)

min Σ_j w_j · [ Σ_{s,r} c(j,s)·x[j,s,r] + B_j·(1 − Σ_{s,r} x[j,s,r]) ]

c(j,s) = (gün − 1) + 0,1·pencere + 5·max(0, gün − due_j)
w_j    = öncelik skoru (+1, türbin kritikse)
B_j    = c(j, ufuktaki son slot) + 10      (backlog maliyeti)

Σ_{s,r} x[j,s,r] ≤ 1                      her iş en çok bir kez
Σ_{j,s∈gün} x[j,s,r] ≤ kapasite_r         kaynak, gün başına
Σ_{j∈müşteri,s∈gün,r} x[j,s,r] ≤ limit    müşteri, gün başına
```

Uyarılar:

* `B_j` işin ufuktaki en kötü slotundan her zaman pahalıdır; böylece uzun
  ufukta gecikme cezaları birikse bile "geç planla" seçeneği "hiç planlama"dan
  ucuz kalır. (Sabit bir ceza kullanılsaydı 10 günlük ufukta model, süresi
  geçmiş kritik işi planlamak yerine backlog'a atmayı tercih ederdi.)
* Backlog cezası, gecikme cezası ve kritik bonusu **yeni kalibre edilmemiş
  sayılardır**.
  Ağırlık sorununu skordan amaç fonksiyonuna taşımamak için alternatif,
  sözlüksel (lexicographic) çözmektir: önce kritik backlog'u en aza indir, o
  değeri sabitleyip toplam backlog'u, sonra ağırlıklı gecikmeyi.
* Eklenmesi gerekenler: iş süresi (çok slotlu işler), sahaya çıkış sabit
  maliyeti `y[saha, gün, kaynak]` (aynı sahadaki işleri gruplamak için), seyahat
  süresi, limit aşımı için yumuşak ceza.
* Ölçek sorun değil: 150 türbin bir saniyenin altında çözülüyor.

## 10. Team yerine contractor/resource pool nasıl tasarlanmalı?

**Yapılan:** `resources.csv` şeması `Resource_ID`, `Resource_Type`,
`Contractor`, `Region`, `Daily_Capacity`. Bölge çoklu (`North|East`) veya
serbest (`*`) olabilir; bir bölgede birden çok kaynak olabilir ve planlayıcı o
gün uygun olanlardan en az bölge kapsayanı (en "yerel" ekibi) seçer; çok
bölgeli ve gezici ekipler esnek kalır. Eski `teams.csv` olduğu gibi okunur. (Eski kodda bölge
üzerinden `merge` yapıldığı için aynı bölgede ikinci bir ekip tanımlamak
türbin satırlarını çoğaltırdı.)

**Sonraki adım:**

| Alan | Neden |
|---|---|
| `Skills` + işte `Task_Type` | Halatlı erişim, kanat onarımı, elektrik aynı ekip değil |
| `Available_From`, `Available_To` | Taşeron sürekli sahada değil |
| `Home_Base` + mesafe matrisi | Bölge etiketi yerine gerçek ulaşım süresi |
| `Daily_Cost`, `Mobilisation_Cost` | Taşeron çağırmanın maliyeti amaç fonksiyonuna girsin |
| Kapasite birimi: ekip-saat | "Günde 1 iş" süreyi yok sayıyor |

## 11. Backlog root cause analytics yeterli mi?

Eski hâliyle hayır:

* Kısıtlar sırayla denetlendiği için yalnızca ilk engel kaydediliyordu.
* Sayım arayüzde serbest metin üzerinde `str.count` ile yapılıyordu; metin
  değişince analiz sessizce bozulurdu.
* "En çok tekrar eden = ana engel" kuralı müdahale edilebilirliği görmüyor.
  Hava 5 slotu, sözleşme 1 slotu engelliyorsa ana engel "hava" olur; oysa
  değiştirilebilecek tek şey o 1 slottur.

**Yapılan:** her slot bütün kısıtlara karşı denetleniyor ve yapılandırılmış
tabloya yazılıyor (`Slot_Diagnostics`). Türbin başına `Primary_Blocker`
(en çok slot engelleyen), `Near_Miss_Slot` (tek kısıta takılan slot) ve
`Actionable` (o kısıt sözleşme ya da kaynak mı) üretiliyor. Öneri metni limiti
o gün hangi türbinin kullandığını adıyla söylüyor.

**Açık:** backlog yaşlanması (türbin kaç plandır backlog'da; kalıcı kayıt
gerektirir) ve karşı-olgusal analiz ("limit +1 olsaydı kaç iş daha
planlanırdı?"; planlayıcıyı gevşetilmiş kısıtla yeniden çalıştırarak).

## 12. 100–150 türbinde hangi veritabanı ve mimari?

Hacim sorun değil: 150 türbin, birkaç bin hasar kaydı pandas ile milisaniyelerde
işlenir. İhtiyaç ölçek değil **kalıcılık, geçmiş ve izlenebilirlik**:

* **PostgreSQL** (tek kullanıcılı pilot için SQLite yeterli). Tablolar:
  `turbines`, `inspections`, `damages` (her denetimde yeni satır, üzerine yazma
  yok), `contracts`, `resources`, `weather_slots`, `runs`, `run_assignments`.
* **Her koşu kayıt altında:** `run_id`, konfigürasyon, girdi sürümü, çıktı.
  "Geçen salı T5 neden backlog'daydı?" sorusu cevaplanabilmeli.
* **Motor paket olarak kalsın** (bu repodaki `windmaint`); üzerine gerekirse
  ince bir FastAPI katmanı. Streamlit pilot için yeterli.
* **Zamanlanmış iş:** her sabah hava tahminini çek, planı yeniden üret,
  değişen atamaları bildir.

Mesaj kuyruğu, dağıtık işlem, mikroservis bu ölçekte gereksizdir.

## 13. ML'den önce hangi gerçek veriler toplanmalı?

1. **Tarihli denetim geçmişi:** aynı `Damage_ID`'nin ardışık denetimlerdeki
   severity değeri ve tarihi. İlerleme modelinin tek girdisi budur.
2. **Hasar nitelikleri:** tip, konum (kök/orta/uç, hücum/firar kenarı), boyut.
3. **Türbin nitelikleri:** model, kanat tipi, yaş, saha koşulları.
4. **Onarım kayıtları:** ne yapıldı, ne zaman, hasar geri geldi mi.
5. **Arıza ve duruş olayları** ve nedenleri (etiket).
6. **Mühendislerin fiilî öncelik kararları:** ağırlık kalibrasyonunun etiketi.
7. **Planlanan ve gerçekleşen:** hangi iş planlandığı gün yapıldı, yapılmadıysa neden.
8. **Tahmin ve gerçekleşen hava:** workability kurallarının doğrulanması.

Madde 6 ve 7 pilot sırasında sistemin kendisi tarafından toplanabilir; pilotun
asıl veri değeri budur.

## 14. SCADA olmadan anlamlı predictive maintenance yapılabilir mi?

Sensörden arıza tahmini anlamında hayır. Denetim verisinden **hasar ilerleme
modeli** anlamında evet, ve bu daha dürüst bir hedef:

* Hasar tipi başına severity geçiş matrisi (Markov): "severity 3 kanat ucu
  erozyonu bir sonraki denetimde %x olasılıkla 4 olur". Yorumlanabilir, az
  veriyle kurulur, derin öğrenme gerektirmez.
* Yeterli veri olduğunda sağkalım analizi (kritik eşiğe kadar geçen süre).

Koşul 13. maddedeki 1–3 numaralı veriler. Sentetik veriyle model eğitmek
görsel etki sağlar, bilgi üretmez. Bu yüzden eski koddaki
`Failure_Probability = skor × 100` kolonu kaldırıldı: olasılık değildi ve
öyle okunacaktı.

## 15. Arayüzde yanıltıcı ya da yanlış adlandırılmış metrikler

| Eski | Sorun | Yeni |
|---|---|---|
| `Failure_Probability` | Skorun 100 ile çarpımı; olasılık değil | Kaldırıldı |
| "AI Recommendations" | ML ya da LLM yok | "Recommendations", "rule-based" ibaresiyle |
| Technical / Contract / Weather = skor × 0,55 / 0,25 / 0,20 | Uydurma döküm; gerçek ağırlıklarla bile uyuşmuyor | Gerçek `*_Contribution` kolonları |
| "Weather Risk" skor bileşeni | Hava skorda yok | Kaldırıldı; hava yalnızca kısıt olarak gösteriliyor |
| `site in ["C", "D"]` hava metni, `site == "B"` sözleşme metni | Veriden değil koddan gelen cümleler | Metinler slot tanılamasından ve sözleşme tablosundan üretiliyor |
| "Contract Pressure" = ortalama time factor | Geçen süre oranı; baskı değil | "Contract period elapsed" |
| "CRITICAL RISK 0,70–1,00 · High backlog escalation probability" | Min–max skorda biri hep kırmızı; olasılık ölçülmüyor | Bantlar "display convention" olarak etiketli; olasılık dili yok |
| Haritada `Average_Risk` | Ortalama, tek kritik türbini gizler | Site durumu en kötü türbine göre; en yüksek skor gösteriliyor |
| Haritada "Healthy" | Yalnızca göreli skor düşük demek | "On plan" |
| "Critical" kategori ile "Critical risk" bandı | Aynı kelime, iki ayrı kavram | Kategori: Critical; bant: High |
| "Team Blocks" | Metin sayımı; ilk-hata maskelemesi | Yapılandırılmış "Resource blocks" |
| Harita hava durumu | Anlık özet tablo; planlayıcının kullandığı tablo değil | Sayfada açıkça belirtiliyor |
| `Due_Status = On Time` | Ufuk 3 gün, en kısa süre 5 gün: hiçbir zaman "Late" olamazdı | Varsayılan ufuk 10 iş günü; filoda "Late" gerçekten oluşuyor. Süre hâlâ plan başlangıcından sayılıyor, denetim tarihinden değil |

## 16. Bu PoC şirket içi pilota nasıl taşınır?

1. **Veriyi gerçek yap.** Bir müşterinin bir sezonluk denetim verisi, gerçek
   site koordinatları, gerçek sözleşme tanımı (availability nasıl ölçülüyor,
   planlı bakım sayılıyor mu). Gizlilik: türbin ve müşteri kimliklerini
   anonimleştiren bir içe aktarma adımı.
2. **Gölge modda çalıştır.** Sistem plan üretir, planlamacı kendi planını
   yapar; ikisi karşılaştırılır. Sistem kimsenin işini yönlendirmez.
3. **Başarıyı önceden tanımla.** Model sıralaması ile mühendis sıralaması
   arasındaki uyum (Kendall τ); backlog nedenlerinin planlamacı tarafından
   doğru bulunma oranı; plan hazırlama süresindeki değişim.
4. **Her itirazı veri olarak topla.** Mühendis sırayı değiştirdiyse nedenini
   tek tıkla kaydetsin. Bu, 4. maddedeki kalibrasyonun etiketidir.
5. **Önce modeli düzelt, sonra genişlet:** türbin-saat bütçesi, iş süreleri,
   gerçek tarihli due, iş tipine göre hava limitleri.
6. **Kalıcılık ekle** (madde 12) ve günlük otomatik koşuya geç.
7. **Sezon sonunda karar ver:** uyum yeterliyse planlamacının ilk taslağını
   sistem üretsin; değilse hangi kriterin eksik olduğu toplanan itirazlardan
   görülür.

ML bu listenin sonunda bile değil; pilotun topladığı veri onu mümkün kılar.
