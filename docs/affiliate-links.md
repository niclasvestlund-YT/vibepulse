# Maintaining Waveshare product links

**Disclosure:** Niclas Vestlund may receive a commission from the marked
affiliate product links in this repository. Waveshare supplied hardware for
development and testing. Receiving a board or linking it does not establish
VibePulse support; the [supported and planned hardware tables](../README.md#supported-screens)
record that separately.

The public affiliate ID **179337** was verified in the owner's Waveshare
account on **2026-09-18**. The link format follows Waveshare's Magento affiliate
guide, Version V4. Account settings, private correspondence and the guide
attachment are not stored in this repository.

## Add or update a link

1. In the Waveshare account, open **Affiliate Panel → Affiliate Link & Ads**.
   Search for the exact part number or SKU and use the generated **Link**.
2. Confirm the destination is the official English shop, `www.waveshare.com`,
   and that the visible **Part No.** and **SKU** match the intended hardware.
3. If the desired variant is absent from the affiliate search, open its
   official SKU link and retain the resulting product URL and query parameters.
   The guide permits appending the affiliate ID to any shop URL: use
   `?&aff_id=179337` when there is no query, or `&aff_id=179337` when there is
   already a query. Do not duplicate or replace an existing affiliate ID.
4. Label the purchase link **affiliate** and keep the commission/hardware
   disclosure next to the table or link. Keep technical references, wiki pages
   and source links as ordinary references.
5. Inspect the destination's selected variant. Record the board revision
   separately: a product URL is not proof that a shipped unit is V2.

For example, the 1.91 touch variant keeps both parameters:

```text
https://www.waveshare.com/esp32-s3-amoled-1.91.htm?sku=28596&aff_id=179337
```

## Variant details verified on 2026-09-18

| Part | SKU | URL detail that must survive |
|---|---|---|
| ESP32-S3-Touch-AMOLED-1.91 | 28596 | `esp32-s3-amoled-1.91.htm?sku=28596` selects the touch model |
| RGB-Matrix-P2.5-64x32-B | 33839 | `rgb-matrix-p2.5-64x32.htm?sku=33839` selects the B variant |
| PSU-5V4A-5.5-2.1-EU | 17679 | `psu-5v-4a-5.5-2.1-us.htm?sku=17679` selects EU despite `us` in the page slug |

The other published links were copied from the account's link generator.
The affiliate ID and product destinations were checked; commission attribution
was not tested with a purchase. Store variants and account terms can change,
so recheck them when adding hardware. Do not copy example IDs from the guide
or publish a promised commission rate.

If Waveshare asks for a source page under **Blog Link For Account Approval**,
provide the public page where the link appears. Preserve any existing
registration and confirm how additional pages should be registered before
replacing it. New documentation does not require changing payment settings.
