# İstanbul ⇄ Japonya Kurban Bayramı uçuş takipçisi

IST ⇄ Tokyo Haneda (HND), Tokyo Narita (NRT) ve Osaka Kansai (KIX) gidiş-dönüş fiyatlarını, ayrıca açık çene rotalarını (Haneda/Narita'ya gidiş, başka havalimanından dönüş: HND-NRT, HND-KIX, NRT-KIX, NRT-HND) günde üç kez (TR saatiyle 08:30, 14:30, 20:30) Google Flights'tan çeker.

- Kurban Bayramı'nı **arife dahil tamamen** kapsayan 10–15 günlük tüm kombinasyonlar. Ramazan Bayramı [ucus-takip](https://github.com/burakkaratopan-droid/ucus-takip) reposunda takip edilir.
- Aktarmasız en ucuz fiyat ve en ucuz 3 tek aktarmalı seçenek
- 40.000 TL altındaki tek aktarmalılar panelde işaretlenir
- Google boş sonuç döndürürse son bilinen fiyat soluk gösterilir
- Alarm: bir aktarmalı 40.000 TL altına inerse ya da aktarmasız fiyat şimdiye kadarki en düşükten en az 1.000 TL aşağı inerse GitHub bir issue açar ve bu e-posta olarak gelir
- Fiyatlar tek yetişkin, ekonomi, gidiş-dönüş toplam, TL

## Dosyalar

| Dosya | Ne işe yarar |
|---|---|
| `config.json` | Kalkış ve varış havalimanları (`destinations`), bayram tarihleri, gün aralığı, 40 bin TL eşiği |
| `tracker.py` | Taramayı yapar, sonuçları `docs/data.json`'a ekler |
| `docs/index.html` | Panel (GitHub Pages) |
| `.github/workflows/track.yml` | Günde 3 kez otomatik çalıştırır |

## Ayar değiştirmek

`config.json` dosyasını GitHub üzerinde düzenleyip kaydetmen yeterli; bir sonraki taramada geçerli olur.
2028 bayramlarını eklemek için `holidays` listesine yeni satır ekle. Havalimanı eklemek/çıkarmak için `destinations` listesini düzenle (her havalimanı taramayı yaklaşık 10 dakika uzatır).

## Elle tarama başlatmak

GitHub'da **Actions → Fiyat taraması → Run workflow**.

## Yerelde çalıştırmak

```bash
pip install -r requirements.txt
python tracker.py
```
