import { createContext, useContext, useMemo, useState } from "react";

const STORAGE_KEY = "p2_lang";

export const LANGUAGES = [
  { id: "en", label: "EN" },
  { id: "az", label: "AZ" },
];

const STRINGS = {
  en: {
    navSearch: "Search",
    navTrending: "Trending",
    navBestSellers: "Product Search",
    navWinning: "Winning Products",

    bestHeading: "Search products",
    bestSubtitle: "Find products across Amazon, Walmart, Temu, Pinterest, Costco and IKEA — then find their manufacturers.",
    bestWhat: "What product?",
    bestFind: "Search",
    bestSearching: "Searching products…",

    selectHint: "Select products to source, or search all with one click.",
    mfrSources: "Manufacturer sources:",
    pickStoreFirst: "Pick at least one store to search.",
    searchStopped: "Search stopped. Nothing was returned for it — run it again when you're ready.",
    pickSourceFirst: "Pick at least one manufacturer source.",
    comingSoon: "Coming soon",
    comingSoonShort: "Soon",
    comingSoonNote: (store) => `${store} is coming soon — it can't be searched yet.`,
    findMfrAll: (n) => `Search manufacturers for all ${n} products`,
    findMfrSelected: (n) => `Search manufacturers for ${n} selected`,
    findMfrSearching: "Finding manufacturers…",
    findMoreLabel: "Find more from:",
    findMoreLoading: (store) => `${store} — searching…`,
    findMoreNoMore: "no more",
    findMoreFailed: "Couldn't fetch more from this store.",
    mfrResultsHeading: "Manufacturers",
    manufacturersFor: "Manufacturers for",
    manufacturersCount: (n) => `${n} listing${n === 1 ? "" : "s"} found`,
    hiddenUnconfirmed: (n) =>
      `${n} more listing${n === 1 ? "" : "s"} hidden — either not confirmed as this product, or no supplier could be identified.`,
    noneConfirmed:
      "None of these could be confirmed as the same product — verify before contacting.",
    sitesUnavailable: (sites) => `${sites} couldn't be searched for this photo.`,
    colSource: "Source",
    colMatch: "Match",
    colNotAvailable: "N/A",
    // The listing on the marketplace, which is what each row is now for.
    colListing: "Listing",
    openListingOn: (site) => `Open on ${site}`,
    listingNoLink: "No link published",
    // Tooltip on the dot in the corner of a product card.
    supplierMark: (n) => `${n} supplier listing${n === 1 ? "" : "s"} found for this item`,

    // How confident we are that a supplier listing is the same product as the
    // photo it was found with, and how that was decided.
    matchTier_identical: "Same photo",
    matchTier_exact: "Same product",
    matchTier_similar: "Similar",
    matchTier_unverified: "Unverified",
    matchUnknown: "—",
    matchByVision: "Photos compared directly",
    matchByHash: "Image-hash only — not visually confirmed",
    // Lens Sourcing has no tier — nothing compared the two products, so these
    // say what Google Lens actually found and claim nothing more.
    matchTier_lens_exact: "Same photo online",
    matchTier_lens_visual: "Looks similar",
    matchByLensExact:
      "Google Lens found this exact image on the supplier's page. The photo matches — the product still isn't independently verified.",
    matchByLensVisual:
      "Google Lens found a similar-looking image. A lead, not a confirmed match.",
    lensNotEnriched: "Supplier details couldn't be read from this listing.",
    mfrLatency: (s) => `Found in ${s}s`,
    msgConnectAccounts: "Connected accounts",
    msgAccountConnected: "Connected",
    msgAccountDisconnected: "Not connected",
    msgConnectSoon: "Connecting accounts is coming soon. Messages are drafted here in the meantime.",
    mfrSearchFailed: "The supplier search couldn't be completed. Please try again.",
    mfrNoResults: "No suppliers found for these products.",
    // Live counts while the supplier search runs. Products are searched one
    // request each and land separately, so both halves are real numbers.
    supplierProgress: (done, total, found) =>
      `${done} of ${total} product${total === 1 ? "" : "s"} checked · ${found} supplier${found === 1 ? "" : "s"} found so far`,
    supplierProgressNone: (done, total) =>
      `${done} of ${total} product${total === 1 ? "" : "s"} checked · no suppliers yet`,
    supplierProgressDeep: (done, total, found) =>
      `${done} of ${total} searched on the marketplaces · ${found} supplier${found === 1 ? "" : "s"} found so far`,
    // Store names scrolling past while a long search runs. The verb rotates each
    // time the list of stores comes round again.
    tickerScanning: (store) => `Scanning ${store}…`,
    tickerReading: (store) => `Reading ${store} listings…`,
    tickerRanking: (store) => `Ranking ${store} best sellers…`,
    tickerCollecting: (store) => `Collecting prices from ${store}…`,
    tickerLensLooking: (store) => `Looking for this photo on ${store}…`,
    tickerLensExact: (store) => `Checking ${store} for an exact match…`,

    // The background supplier lookup that starts as soon as products land.
    prefetchWorking: "Finding suppliers in the background…",
    prefetchReady: (n) => `${n} supplier${n === 1 ? "" : "s"} ready`,
    prefetchReadyNone: "Checked — no suppliers found",
    prefetchTitle:
      "Suppliers for these products are being looked up now, so the manufacturer search comes back straight away.",
    prefetchDismiss: "Hide",
    tickerMatching: (store) => `Matching the photo on ${store}…`,
    tickerOpening: (store) => `Opening ${store} listings…`,
    tickerPricing: (store) => `Reading ${store} prices and MOQ…`,
    tickerProfiling: (store) => `Checking ${store} supplier profiles…`,
    deepSearching: (n) => `No quick match for ${n} product${n === 1 ? "" : "s"} — searching the marketplaces directly (slower)…`,
    colRating: "Rating",
    colPrice: "Price",
    colMoq: "MOQ",

    // Supplier messaging
    selectAllSuppliers: "Select all suppliers",
    selectSupplier: (name) => `Select ${name}`,
    sendMessage: "Send message",
    sendMessageCount: (n) => `Send message (${n})`,
    msgTitle: (n) => `New message to ${n} supplier${n === 1 ? "" : "s"}`,
    msgLabel: "Message",
    msgPlaceholder: "Hi, we're interested in your product. Could you share your best wholesale price, MOQ, and lead time?",
    msgCancel: "Cancel",
    msgSend: (n) => `Send to ${n}`,
    msgSending: "Sending…",
    msgViaSite: (site) => `via ${site}`,
    msgSendVia: "Send via",
    methodEmail: "Email",
    methodWhatsapp: "WhatsApp",
    methodSms: "SMS",
    methodPlatform: "Site inbox",
    msgAiDraft: "Draft with AI",
    msgAiDrafting: "Writing…",
    msgUnreachable: "No method enabled",
    msgYourProduct: "your product",
    msgReachSummary: (r, total) => `${r} of ${total} will receive this`,
    msgPickMethod: "Pick at least one send method",
    msgSentTitle: (n) => `Message sent to ${n} supplier${n === 1 ? "" : "s"}`,
    msgViaEmail: (n) => `${n} by email`,
    msgViaWhatsapp: (n) => `${n} by WhatsApp`,
    msgViaSms: (n) => `${n} by SMS`,
    msgViaInbox: (n) => `${n} by site inbox`,
    msgDone: "Done",

    // AI Best Match
    aiBestMatch: "AI Best Match",
    aiAnalyzing: "Analyzing…",
    aiBadge: "AI Pick",
    aiScore: (n) => `${n}/100`,
    aiShowcaseSub: "The 5 strongest suppliers, ranked by our matching model",
    aiReasonRating: (r) => `top ${r}★ rating`,
    aiReasonPrice: "best price",
    aiReasonMoq: "lowest MOQ",
    aiReasonBalanced: "best overall balance",

    searchHeading: "Find products to source",
    searchSubtitle: "One search across AliExpress, Made-in-China, and Alibaba.",
    modeText: "Text",
    modePhoto: "Photo",
    whatSourcing: "What are you sourcing?",
    queryPlaceholder: "e.g. tumbler",
    sources: "Sources",
    search: "Search",
    uploadThenCrop: "Upload a photo, then crop to the exact item you want to source.",
    choosePhoto: "Choose photo",
    searchingLabel: "Searching… this can take up to a minute.",
    searchingLabelLens: "Searching… Google Lens cross-check can take a few minutes.",
    includeLens: "Also check Google Lens (slower, ~1-3 min, finds exact matches across the web)",
    match: "match",
    noResults: "No results found.",
    loadMore: "Load more",

    trendingHeading: "Trending",
    trendingSubtitle: "Idea → Pinterest inspiration (or upload your own photo) → detect items → source them.",
    ideaLabel: "What's the idea?",
    ideaPlaceholder: "e.g. boho living room decor",
    findInspiration: "Find Inspiration",
    findingInspiration: "Finding inspiration on Pinterest…",
    orUploadOwnPhoto: "Or upload your own photo to detect items in it",
    orCropManually: "Or crop a specific area to search",
    detectingItems: "Detecting items in this image…",
    selectItemsToSearch: "Select item(s) to search for",
    searchSelected: (n) => `Search ${n || ""} selected item(s)`,
    searchingSelected: "Searching selected item(s)…",
    noItemsDetected: "No individually-sourceable items detected in this image.",

    cancel: "Cancel",
    resetCrop: "Reset crop",
    searchThisCrop: "Search this crop",
    searchFullPhoto: "Search full photo",

    // Workbench: nav, command palette
    cpGoTo: "Go to tab",
    cpSectionNavigate: "Navigate",
    cpSectionSearches: "Searches",
    cpSavedSearch: "Saved search",
    cpRecentSearch: "Recent search",
    cpOpenTitle: "Command palette (⌘K / Ctrl+K)",
    wbNoMatches: "No products match the current filters — clear or loosen them.",

    // Photo / Google Lens search
    searchByPhoto: "Search by photo (Google Lens)",
    bestSearchingLens: "Searching with Google Lens… this can take 1–3 minutes.",
    lensCropHint: "Crop to the exact product, then search",
    lensReference: "Search reference",
    exactMatch: "Exact match",
    lensExactTitle: "Google Lens found exact matches.",
    lensExactBody: (n) => `${n} matching product${n === 1 ? "" : "s"} on your selected sites, ranked by demand.`,
    lensSimilarTitle: "No exact match on the selected sites.",
    lensSimilarBody: (n) => `Showing the ${n} closest visual matches instead — pick sites with shoppable listings for exact results.`,
    // Trending searches the whole web rather than a chosen set of stores, so it
    // can't claim anything about "your selected sites".
    lensExactBodyWeb: (n) => `${n} page${n === 1 ? "" : "s"} hosting this exact product, with price and rating read from each.`,
    lensSimilarTitleWeb: "No exact match for this photo.",
    lensSimilarBodyWeb: (n) => `Showing the ${n} closest visual matches instead.`,

    // Workbench components (built keys)
    msTitle: "Market snapshot",
    msToggle: "Toggle market snapshot",
    msResults: "Results",
    msMedianPrice: "Median price",
    msPriceRange: "Price range",
    msAvgRating: "Avg rating",
    msTotalReviews: "Total reviews",
    msCompetition: "Competition",
    msCompLow: "Low",
    msCompMedium: "Medium",
    msCompHigh: "High",
    msCompHint: "Based on median review depth across results",
    msPriceDistribution: "Price distribution",
    msSiteMix: "Site mix",
    msHistProducts: "{0} products",
    rtRefine: "Refine results…",
    rtSortLabel: "Sort by",
    rtSortDefault: "Default order",
    rtSortPriceAsc: "Price: low to high",
    rtSortPriceDesc: "Price: high to low",
    rtSortRating: "Rating (review-weighted)",
    rtSortReviews: "Review count",
    rtMin: "Min",
    rtMax: "Max",
    rtPriceMin: "Minimum price",
    rtPriceMax: "Maximum price",
    rtRatingLabel: "Minimum rating",
    rtRatingAny: "Any",
    rtMinReviews: "Min reviews",
    rtShown: "Showing",
    rtClear: "Clear",
    cpTitle: "Command palette",
    cpSearchLabel: "Search commands",
    cpPlaceholder: "Type a command or search…",
    cpNoResults: "No matching commands",
    cpHints: "↑↓ navigate · ↵ run · esc close",
    ssSaved: "Saved",
    ssRecent: "Recent",
    ssSaveCurrent: "Save this search",
    ssSavedBadge: "Saved",
    ssRemove: "Remove saved search",
    noImage: "No image",
    priceOnRequest: "Price on request",
    priceNotListed: "Price not listed",
    noContactInfo: "No contact info found",
    contactSeller: "Contact Seller",
    unknownSeller: "Unknown seller",
    call: "Call",
    email: "Email",
    availableFrom: (n) => `Available from ${n} sellers`,
    stopSearch: "Stop search",
    exportExcel: "Export to Excel",
    exporting: "Exporting…",
  },
  az: {
    navSearch: "Axtarış",
    navTrending: "Trend",
    navBestSellers: "Məhsul axtarışı",
    navWinning: "Qalib məhsullar",

    bestHeading: "Məhsul axtar",
    bestSubtitle: "Amazon, Walmart, Temu, Pinterest, Costco və IKEA üzrə məhsul tap — sonra istehsalçılarını tap.",
    bestWhat: "Hansı məhsul?",
    bestFind: "Axtar",
    bestSearching: "Məhsullar axtarılır…",

    selectHint: "Mənbələmək üçün məhsul seçin, və ya bir kliklə hamısını axtarın.",
    mfrSources: "İstehsalçı mənbələri:",
    pickStoreFirst: "Axtarış üçün ən azı bir mağaza seçin.",
    searchStopped: "Axtarış dayandırıldı. Nəticə alınmadı — hazır olduğunuzda yenidən başladın.",
    pickSourceFirst: "Ən azı bir istehsalçı mənbəyi seçin.",
    comingSoon: "Tezliklə",
    comingSoonShort: "Tezliklə",
    comingSoonNote: (store) => `${store} tezliklə əlavə olunacaq — hələ axtarıla bilmir.`,
    findMfrAll: (n) => `${n} məhsulun hamısı üçün istehsalçı axtar`,
    findMfrSelected: (n) => `${n} seçilmiş üçün istehsalçı axtar`,
    findMfrSearching: "İstehsalçılar tapılır…",
    findMoreLabel: "Daha çox tap:",
    findMoreLoading: (store) => `${store} — axtarılır…`,
    findMoreNoMore: "daha yoxdur",
    findMoreFailed: "Bu mağazadan daha çox nəticə alınmadı.",
    mfrResultsHeading: "İstehsalçılar",
    manufacturersFor: "İstehsalçılar:",
    manufacturersCount: (n) => `${n} elan tapıldı`,
    hiddenUnconfirmed: (n) =>
      `${n} elan gizlədildi — ya bu məhsul kimi təsdiqlənmədi, ya da təchizatçı müəyyən edilmədi.`,
    noneConfirmed:
      "Bunların heç biri eyni məhsul kimi təsdiqlənmədi — əlaqə saxlamazdan əvvəl yoxlayın.",
    sitesUnavailable: (sites) => `${sites} bu foto üçün axtarıla bilmədi.`,
    colSource: "Mənbə",
    colMatch: "Uyğunluq",
    colNotAvailable: "Yoxdur",
    colListing: "Elan",
    openListingOn: (site) => `${site} saytında aç`,
    listingNoLink: "Keçid yoxdur",
    supplierMark: (n) => `Bu məhsul üçün ${n} təchizatçı elanı tapıldı`,

    matchTier_identical: "Eyni şəkil",
    matchTier_exact: "Eyni məhsul",
    matchTier_similar: "Oxşar",
    matchTier_unverified: "Təsdiqlənməyib",
    matchUnknown: "—",
    matchByVision: "Şəkillər birbaşa müqayisə edilib",
    matchByHash: "Yalnız şəkil heşi — vizual təsdiq yoxdur",
    matchTier_lens_exact: "Eyni şəkil onlayn",
    matchTier_lens_visual: "Oxşar görünür",
    matchByLensExact:
      "Google Lens bu şəkli təchizatçının səhifəsində tapdı. Şəkil uyğundur — məhsul hələ müstəqil təsdiqlənməyib.",
    matchByLensVisual:
      "Google Lens oxşar şəkil tapdı. Bu bir ipucudur, təsdiqlənmiş uyğunluq deyil.",
    lensNotEnriched: "Bu elandan təchizatçı məlumatları oxuna bilmədi.",
    mfrLatency: (s) => `${s}s-də tapıldı`,
    msgConnectAccounts: "Qoşulmuş hesablar",
    msgAccountConnected: "Qoşulub",
    msgAccountDisconnected: "Qoşulmayıb",
    msgConnectSoon: "Hesabların qoşulması tezliklə əlavə olunacaq. Hələlik mesajlar burada hazırlanır.",
    mfrSearchFailed: "Təchizatçı axtarışı tamamlana bilmədi. Yenidən cəhd edin.",
    mfrNoResults: "Bu məhsullar üçün təchizatçı tapılmadı.",
    supplierProgress: (done, total, found) =>
      `${total} məhsuldan ${done} yoxlanıldı · indiyədək ${found} təchizatçı tapıldı`,
    supplierProgressNone: (done, total) =>
      `${total} məhsuldan ${done} yoxlanıldı · hələ təchizatçı yoxdur`,
    supplierProgressDeep: (done, total, found) =>
      `${total} məhsuldan ${done} marketpleyslərdə axtarıldı · indiyədək ${found} təchizatçı tapıldı`,
    tickerScanning: (store) => `${store} skan edilir…`,
    tickerReading: (store) => `${store} elanları oxunur…`,
    tickerRanking: (store) => `${store} ən çox satılanları sıralanır…`,
    tickerCollecting: (store) => `${store} qiymətləri toplanır…`,
    tickerLensLooking: (store) => `Bu şəkil ${store} üzərində axtarılır…`,
    tickerLensExact: (store) => `${store} dəqiq uyğunluq üçün yoxlanılır…`,

    prefetchWorking: "Təchizatçılar arxa fonda axtarılır…",
    prefetchReady: (n) => `${n} təchizatçı hazırdır`,
    prefetchReadyNone: "Yoxlanıldı — təchizatçı tapılmadı",
    prefetchTitle:
      "Bu məhsullar üçün təchizatçılar indi axtarılır, ona görə istehsalçı axtarışı dərhal nəticə verəcək.",
    prefetchDismiss: "Gizlət",
    tickerMatching: (store) => `Şəkil ${store} üzərində uyğunlaşdırılır…`,
    tickerOpening: (store) => `${store} elanları açılır…`,
    tickerPricing: (store) => `${store} qiymət və MOQ oxunur…`,
    tickerProfiling: (store) => `${store} təchizatçı profilləri yoxlanılır…`,
    deepSearching: (n) => `${n} məhsul üçün sürətli uyğunluq yoxdur — birbaşa marketpleyslərdə axtarılır (daha yavaş)…`,
    colRating: "Reytinq",
    colPrice: "Qiymət",
    colMoq: "Min. sifariş",

    // İstehsalçılara mesaj
    selectAllSuppliers: "Bütün istehsalçıları seç",
    selectSupplier: (name) => `${name} seç`,
    sendMessage: "Mesaj göndər",
    sendMessageCount: (n) => `Mesaj göndər (${n})`,
    msgTitle: (n) => `${n} istehsalçıya yeni mesaj`,
    msgLabel: "Mesaj",
    msgPlaceholder: "Salam, məhsulunuzla maraqlanırıq. Ən yaxşı topdan qiymətinizi, minimum sifariş miqdarını və çatdırılma müddətini bildirə bilərsiniz?",
    msgCancel: "Ləğv et",
    msgSend: (n) => `${n} nəfərə göndər`,
    msgSending: "Göndərilir…",
    msgViaSite: (site) => `${site} vasitəsilə`,
    msgSendVia: "Göndərmə üsulu",
    methodEmail: "E-poçt",
    methodWhatsapp: "WhatsApp",
    methodSms: "SMS",
    methodPlatform: "Platforma qutusu",
    msgAiDraft: "AI ilə yaz",
    msgAiDrafting: "Yazılır…",
    msgUnreachable: "Üsul seçilməyib",
    msgYourProduct: "məhsulunuz",
    msgReachSummary: (r, total) => `${total} istehsalçıdan ${r}-i alacaq`,
    msgPickMethod: "Ən azı bir göndərmə üsulu seçin",
    msgSentTitle: (n) => `${n} istehsalçıya mesaj göndərildi`,
    msgViaEmail: (n) => `${n} e-poçtla`,
    msgViaWhatsapp: (n) => `${n} WhatsApp-la`,
    msgViaSms: (n) => `${n} SMS-lə`,
    msgViaInbox: (n) => `${n} platforma qutusu ilə`,
    msgDone: "Hazırdır",

    // AI Ən Yaxşı Uyğunluq
    aiBestMatch: "AI Ən Yaxşı Uyğunluq",
    aiAnalyzing: "Təhlil edilir…",
    aiBadge: "AI Seçimi",
    aiScore: (n) => `${n}/100`,
    aiShowcaseSub: "Uyğunlaşdırma modelimizə görə ən güclü 5 istehsalçı",
    aiReasonRating: (r) => `ən yüksək ${r}★ reytinq`,
    aiReasonPrice: "ən yaxşı qiymət",
    aiReasonMoq: "ən aşağı MOQ",
    aiReasonBalanced: "ən yaxşı ümumi balans",

    searchHeading: "Mənbə üçün məhsul tap",
    searchSubtitle: "AliExpress, Made-in-China və Alibaba üzrə bir axtarış.",
    modeText: "Mətn",
    modePhoto: "Şəkil",
    whatSourcing: "Nə axtarırsınız?",
    queryPlaceholder: "məs. simsiz qulaqlıq",
    sources: "Mənbələr",
    search: "Axtar",
    uploadThenCrop: "Şəkil yükləyin, sonra axtarmaq istədiyiniz məhsulu kəsin.",
    choosePhoto: "Şəkil seç",
    searchingLabel: "Axtarılır… bu bir dəqiqəyə qədər çəkə bilər.",
    searchingLabelLens: "Axtarılır… Google Lens yoxlaması bir neçə dəqiqə çəkə bilər.",
    includeLens: "Google Lens ilə də yoxla (daha yavaş, ~1-3 dəq, internetdə dəqiq uyğunluqlar tapır)",
    match: "uyğunluq",
    noResults: "Nəticə tapılmadı.",
    loadMore: "Daha çox göstər",

    trendingHeading: "Trend",
    trendingSubtitle: "Fikir → Pinterest ilhamı (və ya öz şəklinizi yükləyin) → əşyaları aşkarla → mənbəni tap.",
    ideaLabel: "Fikir nədir?",
    ideaPlaceholder: "məs. boho qonaq otağı dizaynı",
    findInspiration: "İlham tap",
    findingInspiration: "Pinterest-də ilham axtarılır…",
    orUploadOwnPhoto: "Və ya əşyaları aşkarlamaq üçün öz şəklinizi yükləyin",
    orCropManually: "Və ya axtarmaq üçün müəyyən bir hissəni kəsin",
    detectingItems: "Bu şəkildə əşyalar aşkarlanır…",
    selectItemsToSearch: "Axtarmaq üçün əşya(lar) seçin",
    searchSelected: (n) => `${n || ""} seçilmiş əşyanı axtar`,
    searchingSelected: "Seçilmiş əşya(lar) axtarılır…",
    noItemsDetected: "Bu şəkildə ayrıca mənbələnə bilən əşya aşkarlanmadı.",

    cancel: "Ləğv et",
    resetCrop: "Kəsimi sıfırla",
    searchThisCrop: "Bu kəsimi axtar",
    searchFullPhoto: "Tam şəkli axtar",

    // Workbench: naviqasiya, əmr paneli
    cpGoTo: "Tab-a keç",
    cpSectionNavigate: "Naviqasiya",
    cpSectionSearches: "Axtarışlar",
    cpSavedSearch: "Saxlanmış axtarış",
    cpRecentSearch: "Son axtarış",
    cpOpenTitle: "Əmr paneli (⌘K / Ctrl+K)",
    wbNoMatches: "Cari filtrlərə uyğun məhsul yoxdur — filtrləri təmizləyin və ya yumşaldın.",

    // Şəkillə / Google Lens axtarışı
    searchByPhoto: "Şəkillə axtar (Google Lens)",
    bestSearchingLens: "Google Lens ilə axtarılır… bu 1–3 dəqiqə çəkə bilər.",
    lensCropHint: "Dəqiq məhsulu kəsin, sonra axtarın",
    lensReference: "Axtarış nümunəsi",
    exactMatch: "Dəqiq uyğunluq",
    lensExactTitle: "Google Lens dəqiq uyğunluqlar tapdı.",
    lensExactBody: (n) => `Seçdiyiniz saytlarda ${n} uyğun məhsul, tələbə görə sıralandı.`,
    lensSimilarTitle: "Seçilmiş saytlarda dəqiq uyğunluq yoxdur.",
    lensSimilarBody: (n) => `Bunun əvəzinə ən yaxın ${n} vizual uyğunluğu göstəririk — dəqiq nəticə üçün alış-veriş listinqi olan saytları seçin.`,
    // Trend səhifəsi seçilmiş mağazalarda deyil, bütün internetdə axtarır.
    lensExactBodyWeb: (n) => `Bu məhsulu yerləşdirən ${n} səhifə — qiymət və reytinq hər birindən oxundu.`,
    lensSimilarTitleWeb: "Bu şəkil üçün dəqiq uyğunluq tapılmadı.",
    lensSimilarBodyWeb: (n) => `Bunun əvəzinə ən yaxın ${n} vizual uyğunluğu göstəririk.`,

    // Workbench komponentləri
    msTitle: "Bazar icmalı",
    msToggle: "Bazar icmalını aç/bağla",
    msResults: "Nəticələr",
    msMedianPrice: "Median qiymət",
    msPriceRange: "Qiymət aralığı",
    msAvgRating: "Orta reytinq",
    msTotalReviews: "Ümumi rəylər",
    msCompetition: "Rəqabət",
    msCompLow: "Aşağı",
    msCompMedium: "Orta",
    msCompHigh: "Yüksək",
    msCompHint: "Nəticələr üzrə median rəy sayına əsaslanır",
    msPriceDistribution: "Qiymət paylanması",
    msSiteMix: "Sayt tərkibi",
    msHistProducts: "{0} məhsul",
    rtRefine: "Nəticələr içində axtar…",
    rtSortLabel: "Sıralama",
    rtSortDefault: "Standart sıralama",
    rtSortPriceAsc: "Qiymət: artan",
    rtSortPriceDesc: "Qiymət: azalan",
    rtSortRating: "Reytinq (rəy sayına görə çəkili)",
    rtSortReviews: "Rəy sayı",
    rtMin: "Min",
    rtMax: "Maks",
    rtPriceMin: "Minimum qiymət",
    rtPriceMax: "Maksimum qiymət",
    rtRatingLabel: "Minimum reytinq",
    rtRatingAny: "Hamısı",
    rtMinReviews: "Min rəy sayı",
    rtShown: "Göstərilir",
    rtClear: "Təmizlə",
    cpTitle: "Əmr paneli",
    cpSearchLabel: "Əmrləri axtar",
    cpPlaceholder: "Əmr yazın və ya axtarın…",
    cpNoResults: "Uyğun əmr tapılmadı",
    cpHints: "↑↓ naviqasiya · ↵ icra et · esc bağla",
    ssSaved: "Yadda saxlanılan",
    ssRecent: "Son axtarışlar",
    ssSaveCurrent: "Bu axtarışı yadda saxla",
    ssSavedBadge: "Yadda saxlanıb",
    ssRemove: "Yadda saxlanılan axtarışı sil",
    noImage: "Şəkil yoxdur",
    priceOnRequest: "Qiymət sorğu ilə",
    priceNotListed: "Qiymət göstərilməyib",
    noContactInfo: "Əlaqə məlumatı tapılmadı",
    contactSeller: "Satıcı ilə əlaqə",
    unknownSeller: "Naməlum satıcı",
    call: "Zəng et",
    email: "E-poçt",
    availableFrom: (n) => `${n} satıcıdan mövcuddur`,
    stopSearch: "Axtarışı dayandır",
    exportExcel: "Excel-ə ixrac et",
    exporting: "İxrac edilir…",
  },
};

const I18nContext = createContext(null);

export function I18nProvider({ children }) {
  const [lang, setLangState] = useState(() => {
    const stored = typeof localStorage !== "undefined" && localStorage.getItem(STORAGE_KEY);
    return stored && STRINGS[stored] ? stored : "en";
  });

  function setLang(next) {
    if (!STRINGS[next]) return;
    setLangState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // localStorage unavailable — language choice just won't persist
    }
  }

  const value = useMemo(() => {
    const dict = STRINGS[lang];
    return { lang, setLang, t: (key, ...args) => {
      const entry = dict[key] ?? STRINGS.en[key] ?? key;
      return typeof entry === "function" ? entry(...args) : entry;
    } };
  }, [lang]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within I18nProvider");
  return ctx;
}
