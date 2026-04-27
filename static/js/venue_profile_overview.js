(() => {
  const toolbarForm = document.getElementById("venueProfileInventoryToolbar");
  if (!toolbarForm) return;

  const searchInput = document.getElementById("venueProfileSearch");
  const filterSelect = document.getElementById("venueProfileFilter");
  const sortSelect = document.getElementById("venueProfileSort");
  const segmentButtons = Array.from(document.querySelectorAll("[data-segment-value]"));
  const summaryFilterButtons = Array.from(document.querySelectorAll("[data-summary-filter]"));
  const clearButton = document.getElementById("venueProfileClearFilters");
  const expandToggleButton = document.getElementById("venueProfileExpandToggle");
  const resultCount = document.getElementById("venueProfileResultCount");
  const emptyState = document.getElementById("venueProfileClientEmptyState");
  const desktopList = document.getElementById("venueProfileDesktopList");
  const mobileList = document.getElementById("venueProfileMobileList");
  const activeFiltersWrap = document.getElementById("venueProfileActiveFilters");
  const activeFilterList = document.getElementById("venueProfileActiveFilterList");
  const exportLinks = Array.from(document.querySelectorAll("[data-export-base-href][data-export-scope]"));

  const searchDebounceMs = 180;
  const defaultFilter = "all";
  const defaultSort = "needs_action";
  const defaultSegment = "all";
  const validFilters = new Set(Array.from(filterSelect.options).map((option) => option.value));
  const validSorts = new Set(Array.from(sortSelect.options).map((option) => option.value));
  const validSegments = new Set(["all", "needs_action", "review", "assets"]);
  const segmentLabels = {
    needs_action: "Needs Action",
    review: "Review",
    assets: "Assets",
  };
  const filterLabels = Array.from(filterSelect.options).reduce((labels, option) => {
    labels[option.value] = option.textContent.trim();
    return labels;
  }, {});
  const summaryPresetMap = {
    asset_issues: { segment: "assets", filter: defaultFilter, sort: defaultSort },
    review: { segment: "review", filter: defaultFilter, sort: "review_first" },
    review_queue: { segment: "review", filter: defaultFilter, sort: "review_first" },
    count_attention: { segment: defaultSegment, filter: "count_attention", sort: defaultSort },
  };

  let searchDebounceHandle = null;

  function getListGroups(listEl) {
    if (!listEl) return [];
    return Array.from(listEl.children).filter((child) => child.classList.contains("venue-profile-group"));
  }

  function getActiveSegment() {
    return segmentButtons.find((button) => button.classList.contains("is-active"))?.dataset.segmentValue || defaultSegment;
  }

  function setActiveSegment(segmentValue) {
    const normalized = validSegments.has(segmentValue) ? segmentValue : defaultSegment;
    segmentButtons.forEach((button) => {
      const isActive = button.dataset.segmentValue === normalized;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
  }

  function parseNumericData(element, key, fallbackValue) {
    const parsedValue = Number(element.dataset[key] || "");
    return Number.isFinite(parsedValue) ? parsedValue : fallbackValue;
  }

  function hasWordBoundaryPrefix(text, query) {
    if (!query) return false;
    const safeQuery = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(`(^|\\W)${safeQuery}`).test(text);
  }

  function getSearchRank(element, query) {
    if (!query) return 0;
    const itemText = (element.dataset.itemName || "").toLowerCase();
    const familyText = (element.dataset.family || "").toLowerCase();
    const searchText = (element.dataset.name || "").toLowerCase();
    if (itemText.startsWith(query)) return 0;
    if (hasWordBoundaryPrefix(itemText, query)) return 1;
    if (itemText.includes(query)) return 2;
    if (familyText.startsWith(query)) return 3;
    if (hasWordBoundaryPrefix(familyText, query)) return 4;
    if (familyText.includes(query)) return 5;
    if (searchText.includes(query)) return 6;
    return null;
  }

  function hasActiveCriteria(query, segmentValue, filterValue) {
    return Boolean(query || segmentValue !== defaultSegment || filterValue !== defaultFilter);
  }

  function matchesSegment(element, segmentValue) {
    if (segmentValue === defaultSegment) return true;
    if (segmentValue === "needs_action") return element.dataset.operationalIssue === "true";
    if (segmentValue === "review") return element.dataset.reviewIssue === "true";
    if (segmentValue === "assets") {
      return element.dataset.trackingMode === "singleton_asset"
        || element.dataset.hasSingleton === "true"
        || element.dataset.trackingMode === "mixed";
    }
    return true;
  }

  function matchesLeafFilter(element, filterValue) {
    if (filterValue === defaultFilter) return true;
    if (filterValue === "count_attention") return element.dataset.countGap === "true";
    if (filterValue === "status_attention") return element.dataset.statusGap === "true";
    if (filterValue === "singleton_asset") return element.dataset.trackingMode === "singleton_asset";
    if (filterValue === "quantity") return element.dataset.trackingMode === "quantity";
    if (filterValue === "families") return false;
    return true;
  }

  function matchesGroupSummaryFilter(groupEl, filterValue) {
    if (filterValue === defaultFilter) return true;
    if (filterValue === "families") return groupEl.dataset.kind === "family";
    if (filterValue === "count_attention") return groupEl.dataset.countGap === "true";
    if (filterValue === "status_attention") return groupEl.dataset.statusGap === "true";
    if (filterValue === "singleton_asset") return groupEl.dataset.trackingMode === "singleton_asset" || groupEl.dataset.hasSingleton === "true";
    if (filterValue === "quantity") return groupEl.dataset.trackingMode === "quantity" || groupEl.dataset.hasQuantity === "true";
    return false;
  }

  function matchesLeaf(element, query, segmentValue, filterValue) {
    if (!matchesSegment(element, segmentValue)) return false;
    if (!matchesLeafFilter(element, filterValue)) return false;
    return getSearchRank(element, query) !== null;
  }

  function collapseIfHidden(toggleButton) {
    const targetSelector = toggleButton?.getAttribute("data-bs-target");
    if (!targetSelector) return;

    const detailEl = document.querySelector(targetSelector);
    if (!detailEl || !detailEl.classList.contains("show")) return;

    const collapseInstance = window.bootstrap?.Collapse?.getOrCreateInstance(detailEl, { toggle: false });
    collapseInstance?.hide();
  }

  function syncFamilyExpandedState(groupId, expanded) {
    document.querySelectorAll(`[data-group-id="${groupId}"][data-kind="family"]`).forEach((groupEl) => {
      groupEl.dataset.manuallyExpanded = expanded ? "true" : "false";
    });
  }

  function getFamilyCollapseElement(groupEl) {
    const toggleButton = groupEl.querySelector("[data-family-toggle][aria-controls]");
    const controlsId = toggleButton?.getAttribute("aria-controls");
    if (!controlsId) return null;
    return document.getElementById(controlsId);
  }

  function getItemCollapseElement(groupEl) {
    const toggleButton = groupEl.querySelector(".venue-profile-inventory-toggle, .venue-profile-mobile-toggle");
    const targetSelector = toggleButton?.getAttribute("data-bs-target");
    if (!targetSelector) return null;
    return groupEl.querySelector(targetSelector) || document.querySelector(targetSelector);
  }

  function getGroupCollapseElement(groupEl) {
    if (groupEl.dataset.kind === "family") return getFamilyCollapseElement(groupEl);
    return getItemCollapseElement(groupEl);
  }

  function getNestedItems(groupEl) {
    return Array.from(groupEl.querySelectorAll('.venue-profile-group[data-nested="true"]'));
  }

  function updateItemToggleUi(groupEl, expanded) {
    groupEl.querySelectorAll(".venue-profile-inventory-toggle, .venue-profile-mobile-toggle").forEach((button) => {
      button.classList.toggle("is-open", expanded);
      button.setAttribute("aria-expanded", expanded ? "true" : "false");
    });
  }

  function updateFamilyToggleUi(groupEl, expanded) {
    groupEl.querySelectorAll("[data-family-toggle]").forEach((button) => {
      button.classList.toggle("is-open", expanded);
      button.setAttribute("aria-expanded", expanded ? "true" : "false");
    });
  }

  function setItemCollapseState(groupEl, expanded) {
    const detailEl = getItemCollapseElement(groupEl);
    if (!detailEl || !window.bootstrap?.Collapse) return;

    const collapseInstance = window.bootstrap.Collapse.getOrCreateInstance(detailEl, { toggle: false });
    if (expanded) collapseInstance.show();
    else collapseInstance.hide();
  }

  function updateFamilyExpansion(groupEl, expanded) {
    const collapseEl = getFamilyCollapseElement(groupEl);
    updateFamilyToggleUi(groupEl, expanded);

    if (!collapseEl || !window.bootstrap?.Collapse) return;

    const collapseInstance = window.bootstrap.Collapse.getOrCreateInstance(collapseEl, { toggle: false });
    if (expanded) collapseInstance.show();
    else collapseInstance.hide();
  }

  function applyGroupState(groupEl, query, segmentValue, filterValue) {
    const isFamily = groupEl.dataset.kind === "family";
    const activeCriteria = hasActiveCriteria(query, segmentValue, filterValue);

    if (!isFamily) {
      const isVisible = matchesLeaf(groupEl, query, segmentValue, filterValue);
      groupEl.hidden = !isVisible;
      if (!isVisible) {
        collapseIfHidden(groupEl.querySelector(".venue-profile-inventory-toggle, .venue-profile-mobile-toggle"));
      }
      return isVisible ? 1 : 0;
    }

    const nestedItems = getNestedItems(groupEl);
    const parentMatchesSearch = query ? getSearchRank(groupEl, query) !== null : false;
    const parentMatchesFilters = matchesSegment(groupEl, segmentValue) && matchesGroupSummaryFilter(groupEl, filterValue);
    let visibleChildCount = 0;

    nestedItems.forEach((itemEl) => {
      const directMatch = matchesLeaf(itemEl, query, segmentValue, filterValue);
      const matchesFilterOnly = matchesLeaf(itemEl, "", segmentValue, filterValue);
      let isVisibleByFilter = true;

      if (activeCriteria) {
        if (filterValue === "families") {
          isVisibleByFilter = true;
        } else if (parentMatchesSearch) {
          isVisibleByFilter = matchesFilterOnly;
        } else {
          isVisibleByFilter = directMatch;
        }
      }

      itemEl.dataset.filterVisible = isVisibleByFilter ? "true" : "false";
      if (isVisibleByFilter) visibleChildCount += 1;
    });

    const groupMatchesQuery = !query || parentMatchesSearch;
    const groupVisible = !activeCriteria
      || ((groupMatchesQuery && parentMatchesFilters) || (filterValue !== "families" && visibleChildCount > 0));

    groupEl.hidden = !groupVisible;
    if (!groupVisible) {
      nestedItems.forEach((itemEl) => {
        itemEl.hidden = true;
        collapseIfHidden(itemEl.querySelector(".venue-profile-inventory-toggle, .venue-profile-mobile-toggle"));
      });
      updateFamilyExpansion(groupEl, false);
      return 0;
    }

    const manuallyExpanded = groupEl.dataset.manuallyExpanded === "true";
    const autoExpanded = activeCriteria && filterValue !== "families" && visibleChildCount > 0;
    const expanded = autoExpanded || manuallyExpanded;

    groupEl.dataset.autoExpanded = autoExpanded ? "true" : "false";

    nestedItems.forEach((itemEl) => {
      const isVisibleByFilter = itemEl.dataset.filterVisible !== "false";
      itemEl.hidden = !isVisibleByFilter;
      if (!expanded || !isVisibleByFilter) {
        collapseIfHidden(itemEl.querySelector(".venue-profile-inventory-toggle, .venue-profile-mobile-toggle"));
      }
    });

    updateFamilyExpansion(groupEl, expanded);

    return activeCriteria && filterValue !== "families" ? visibleChildCount : nestedItems.length;
  }

  function attentionRank(element) {
    return {
      healthy: 0,
      warning: 1,
      critical: 2,
    }[element.dataset.attentionLevel || "healthy"] || 0;
  }

  function compareGroups(a, b, sortKey, searchQuery) {
    if (searchQuery) {
      const rankA = getSearchRank(a, searchQuery);
      const rankB = getSearchRank(b, searchQuery);
      const normalizedRankA = rankA == null ? Number.POSITIVE_INFINITY : rankA;
      const normalizedRankB = rankB == null ? Number.POSITIVE_INFINITY : rankB;
      if (normalizedRankA !== normalizedRankB) return normalizedRankA - normalizedRankB;
    }

    const nameA = (a.dataset.itemName || "").toLowerCase();
    const nameB = (b.dataset.itemName || "").toLowerCase();

    if (sortKey === "alphabetical") {
      return nameA.localeCompare(nameB);
    }

    if (sortKey === "review_first") {
      const reviewA = a.dataset.reviewIssue === "true" ? 1 : 0;
      const reviewB = b.dataset.reviewIssue === "true" ? 1 : 0;
      if (reviewA !== reviewB) return reviewB - reviewA;

      const operationalA = a.dataset.operationalIssue === "true" ? 1 : 0;
      const operationalB = b.dataset.operationalIssue === "true" ? 1 : 0;
      if (operationalA !== operationalB) return operationalB - operationalA;
    }

    if (sortKey === "stalest") {
      const updatedA = parseNumericData(a, "lastUpdated", 0);
      const updatedB = parseNumericData(b, "lastUpdated", 0);
      if (updatedA !== updatedB) return updatedA - updatedB;
      return nameA.localeCompare(nameB);
    }

    if (sortKey === "lowest_count_coverage") {
      const coverageA = parseNumericData(a, "countCoverage", Number.POSITIVE_INFINITY);
      const coverageB = parseNumericData(b, "countCoverage", Number.POSITIVE_INFINITY);
      if (coverageA !== coverageB) return coverageA - coverageB;
      return nameA.localeCompare(nameB);
    }

    if (sortKey === "recent") {
      const updatedA = parseNumericData(a, "lastUpdated", 0);
      const updatedB = parseNumericData(b, "lastUpdated", 0);
      if (updatedA !== updatedB) return updatedB - updatedA;
      return nameA.localeCompare(nameB);
    }

    const operationalA = a.dataset.operationalIssue === "true" ? 1 : 0;
    const operationalB = b.dataset.operationalIssue === "true" ? 1 : 0;
    if (operationalA !== operationalB) return operationalB - operationalA;

    const reviewA = a.dataset.reviewIssue === "true" ? 1 : 0;
    const reviewB = b.dataset.reviewIssue === "true" ? 1 : 0;
    if (reviewA !== reviewB) return reviewB - reviewA;

    const attentionA = attentionRank(a);
    const attentionB = attentionRank(b);
    if (attentionA !== attentionB) return attentionB - attentionA;

    const updatedA = parseNumericData(a, "lastUpdated", 0);
    const updatedB = parseNumericData(b, "lastUpdated", 0);
    if (updatedA !== updatedB) return updatedA - updatedB;

    return nameA.localeCompare(nameB);
  }

  function updateResultCount(visibleCount) {
    if (!resultCount) return;
    const totalCount = Number(resultCount.dataset.total || visibleCount || 0);
    resultCount.textContent = `Showing ${visibleCount} of ${totalCount} tracked items`;
  }

  function syncActionUrls() {
    const nextValue = `${window.location.pathname}${window.location.search}`;
    document.querySelectorAll("[data-venue-profile-base-href]").forEach((link) => {
      const baseHref = link.getAttribute("data-venue-profile-base-href");
      if (!baseHref) return;
      const nextUrl = new URL(baseHref, window.location.origin);
      nextUrl.searchParams.set("next", nextValue);
      link.href = `${nextUrl.pathname}${nextUrl.search}`;
    });
  }

  function syncInventoryExportUrls() {
    if (!exportLinks.length) return;

    const searchValue = (searchInput.value || "").trim();
    const segmentValue = getActiveSegment();
    const filterValue = filterSelect.value || defaultFilter;
    const sortValue = sortSelect.value || defaultSort;

    exportLinks.forEach((link) => {
      const baseHref = link.getAttribute("data-export-base-href");
      if (!baseHref) return;

      const scope = link.getAttribute("data-export-scope") || "filtered";
      const nextUrl = new URL(baseHref, window.location.origin);
      nextUrl.searchParams.set("scope", scope);

      if (sortValue && sortValue !== defaultSort) {
        nextUrl.searchParams.set("inventory_sort", sortValue);
      } else {
        nextUrl.searchParams.delete("inventory_sort");
      }

      if (scope === "filtered") {
        if (searchValue) nextUrl.searchParams.set("inventory_q", searchValue);
        else nextUrl.searchParams.delete("inventory_q");

        if (segmentValue !== defaultSegment) nextUrl.searchParams.set("inventory_segment", segmentValue);
        else nextUrl.searchParams.delete("inventory_segment");

        if (filterValue !== defaultFilter) nextUrl.searchParams.set("inventory_filter", filterValue);
        else nextUrl.searchParams.delete("inventory_filter");
      } else {
        nextUrl.searchParams.delete("inventory_q");
        nextUrl.searchParams.delete("inventory_segment");
        nextUrl.searchParams.delete("inventory_filter");
      }

      link.href = `${nextUrl.pathname}${nextUrl.search}`;
    });
  }

  function syncInventoryUrl() {
    const nextUrl = new URL(window.location.href);
    const nextSearch = (searchInput.value || "").trim();
    const nextSegment = getActiveSegment();
    const nextFilter = filterSelect.value || defaultFilter;
    const nextSort = sortSelect.value || defaultSort;

    if (nextSearch) nextUrl.searchParams.set("inventory_q", nextSearch);
    else nextUrl.searchParams.delete("inventory_q");

    if (nextSegment !== defaultSegment) nextUrl.searchParams.set("inventory_segment", nextSegment);
    else nextUrl.searchParams.delete("inventory_segment");

    if (nextFilter !== defaultFilter) nextUrl.searchParams.set("inventory_filter", nextFilter);
    else nextUrl.searchParams.delete("inventory_filter");

    if (nextSort !== defaultSort) nextUrl.searchParams.set("inventory_sort", nextSort);
    else nextUrl.searchParams.delete("inventory_sort");

    window.history.replaceState({}, "", nextUrl);
    syncActionUrls();
    syncInventoryExportUrls();
  }

  function hasNonDefaultToolbarState() {
    return Boolean(
      (searchInput.value || "").trim()
      || getActiveSegment() !== defaultSegment
      || (filterSelect.value || defaultFilter) !== defaultFilter
      || (sortSelect.value || defaultSort) !== defaultSort
    );
  }

  function renderActiveFilters() {
    if (!activeFiltersWrap || !activeFilterList) return;

    const activeItems = [];
    const searchValue = (searchInput.value || "").trim();
    const segmentValue = getActiveSegment();
    const filterValue = filterSelect.value || defaultFilter;

    if (searchValue) activeItems.push({ type: "search", value: searchValue, label: `Search: ${searchValue}` });
    if (segmentValue !== defaultSegment) activeItems.push({ type: "segment", value: segmentValue, label: segmentLabels[segmentValue] || segmentValue });
    if (filterValue !== defaultFilter) activeItems.push({ type: "filter", value: filterValue, label: filterLabels[filterValue] || filterValue });

    activeFilterList.innerHTML = "";
    activeFiltersWrap.classList.toggle("is-inactive", activeItems.length === 0);
    activeFiltersWrap.setAttribute("aria-hidden", activeItems.length === 0 ? "true" : "false");

    if (clearButton) {
      const isInactive = !hasNonDefaultToolbarState();
      clearButton.classList.toggle("is-inactive", isInactive);
      clearButton.disabled = isInactive;
      clearButton.setAttribute("aria-hidden", isInactive ? "true" : "false");
    }

    activeItems.forEach((item) => {
      const button = document.createElement("button");
      const labelSpan = document.createElement("span");
      const icon = document.createElement("i");
      button.type = "button";
      button.className = "venue-profile-active-filter-pill";
      button.dataset.filterType = item.type;
      button.dataset.filterValue = item.value;
      button.setAttribute("aria-label", `Remove ${item.label} filter`);
      labelSpan.textContent = item.label;
      icon.className = "bi bi-x-lg";
      icon.setAttribute("aria-hidden", "true");
      button.append(labelSpan, icon);
      activeFilterList.appendChild(button);
    });
  }

  function updateExpandToggleLabel() {
    if (!expandToggleButton) return;
    const usingMobile = window.matchMedia("(max-width: 1199.98px)").matches;
    const visibleGroups = getListGroups(usingMobile ? mobileList : desktopList).filter((groupEl) => !groupEl.hidden);
    if (!visibleGroups.length) {
      expandToggleButton.textContent = "Expand all";
      expandToggleButton.disabled = true;
      return;
    }

    expandToggleButton.disabled = false;
    const allExpanded = visibleGroups.every((groupEl) => {
      const collapseEl = getGroupCollapseElement(groupEl);
      return Boolean(collapseEl?.classList.contains("show"));
    });
    expandToggleButton.textContent = allExpanded ? "Collapse all" : "Expand all";
  }

  function applyInventoryView() {
    const searchQuery = (searchInput.value || "").trim().toLowerCase();
    const segmentValue = getActiveSegment();
    const filterValue = filterSelect.value || defaultFilter;
    const sortKey = sortSelect.value || defaultSort;
    let visibleDesktopCount = 0;
    let visibleMobileCount = 0;

    [
      { list: desktopList, assignCount: (count) => { visibleDesktopCount = count; } },
      { list: mobileList, assignCount: (count) => { visibleMobileCount = count; } },
    ].forEach(({ list, assignCount }) => {
      const groups = getListGroups(list);
      groups.sort((a, b) => compareGroups(a, b, sortKey, searchQuery));
      groups.forEach((groupEl) => list.appendChild(groupEl));

      let visibleCount = 0;
      groups.forEach((groupEl) => {
        visibleCount += applyGroupState(groupEl, searchQuery, segmentValue, filterValue);
      });
      assignCount(visibleCount);
    });

    const visibleCount = window.matchMedia("(max-width: 1199.98px)").matches ? visibleMobileCount : visibleDesktopCount;
    if (emptyState) emptyState.classList.toggle("d-none", visibleCount > 0);

    updateResultCount(visibleCount);
    renderActiveFilters();
    updateExpandToggleLabel();
    syncInventoryUrl();
  }

  function queueInventoryView(immediate = false) {
    if (searchDebounceHandle) {
      window.clearTimeout(searchDebounceHandle);
      searchDebounceHandle = null;
    }

    if (immediate) {
      applyInventoryView();
      return;
    }

    searchDebounceHandle = window.setTimeout(() => {
      applyInventoryView();
    }, searchDebounceMs);
  }

  function resetInventoryFilters() {
    searchInput.value = "";
    filterSelect.value = defaultFilter;
    sortSelect.value = defaultSort;
    setActiveSegment(defaultSegment);
  }

  function applySummaryPreset(preset) {
    const config = summaryPresetMap[preset];
    if (!config) return;
    setActiveSegment(config.segment);
    filterSelect.value = config.filter;
    sortSelect.value = config.sort;
    queueInventoryView(true);
  }

  toolbarForm.addEventListener("submit", (event) => {
    event.preventDefault();
    queueInventoryView(true);
  });

  searchInput.addEventListener("input", () => {
    queueInventoryView(false);
  });

  filterSelect.addEventListener("change", () => {
    queueInventoryView(true);
  });

  sortSelect.addEventListener("change", () => {
    queueInventoryView(true);
  });

  segmentButtons.forEach((button) => {
    button.addEventListener("click", () => {
      setActiveSegment(button.dataset.segmentValue);
      queueInventoryView(true);
    });
  });

  summaryFilterButtons.forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      applySummaryPreset(button.dataset.summaryFilter || "");
    });
  });

  clearButton?.addEventListener("click", () => {
    resetInventoryFilters();
    queueInventoryView(true);
  });

  activeFilterList?.addEventListener("click", (event) => {
    const button = event.target.closest(".venue-profile-active-filter-pill");
    if (!button) return;

    if (button.dataset.filterType === "search") searchInput.value = "";
    if (button.dataset.filterType === "segment") setActiveSegment(defaultSegment);
    if (button.dataset.filterType === "filter") filterSelect.value = defaultFilter;

    queueInventoryView(true);
  });

  expandToggleButton?.addEventListener("click", () => {
    const usingMobile = window.matchMedia("(max-width: 1199.98px)").matches;
    const visibleGroups = getListGroups(usingMobile ? mobileList : desktopList).filter((groupEl) => !groupEl.hidden);
    if (!visibleGroups.length) return;

    const shouldExpand = expandToggleButton.textContent !== "Collapse all";

    visibleGroups.forEach((groupEl) => {
      if (groupEl.dataset.kind === "family") {
        syncFamilyExpandedState(groupEl.dataset.groupId, shouldExpand);
      } else {
        setItemCollapseState(groupEl, shouldExpand);
      }
    });

    applyInventoryView();
  });

  document.querySelectorAll("[data-family-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const groupEl = button.closest("[data-group-id][data-kind='family']");
      if (!groupEl) return;

      const nextExpanded = groupEl.dataset.manuallyExpanded !== "true";
      syncFamilyExpandedState(groupEl.dataset.groupId, nextExpanded);
      applyInventoryView();
    });
  });

  document.querySelectorAll(".venue-profile-inventory-detail").forEach((collapseEl) => {
    collapseEl.addEventListener("shown.bs.collapse", () => {
      const groupEl = collapseEl.closest(".venue-profile-group");
      if (!groupEl) return;
      updateItemToggleUi(groupEl, true);
      updateExpandToggleLabel();
    });

    collapseEl.addEventListener("hidden.bs.collapse", () => {
      const groupEl = collapseEl.closest(".venue-profile-group");
      if (!groupEl) return;
      updateItemToggleUi(groupEl, false);
      updateExpandToggleLabel();
    });
  });

  document.querySelectorAll(".venue-profile-family-children, .venue-profile-mobile-family-children").forEach((collapseEl) => {
    collapseEl.addEventListener("shown.bs.collapse", () => {
      const groupEl = collapseEl.closest(".venue-profile-group[data-kind='family']");
      if (!groupEl) return;
      updateFamilyToggleUi(groupEl, true);
      updateExpandToggleLabel();
    });

    collapseEl.addEventListener("hidden.bs.collapse", () => {
      const groupEl = collapseEl.closest(".venue-profile-group[data-kind='family']");
      if (!groupEl) return;
      updateFamilyToggleUi(groupEl, false);
      updateExpandToggleLabel();
    });
  });

  window.addEventListener("resize", updateExpandToggleLabel);

  const currentUrl = new URL(window.location.href);
  const initialSearch = currentUrl.searchParams.get("inventory_q") || "";
  const initialSegment = currentUrl.searchParams.get("inventory_segment") || defaultSegment;
  const initialFilter = currentUrl.searchParams.get("inventory_filter") || defaultFilter;
  const initialSort = currentUrl.searchParams.get("inventory_sort") || defaultSort;

  searchInput.value = initialSearch;
  setActiveSegment(validSegments.has(initialSegment) ? initialSegment : defaultSegment);
  filterSelect.value = validFilters.has(initialFilter) ? initialFilter : defaultFilter;
  sortSelect.value = validSorts.has(initialSort) ? initialSort : defaultSort;

  document.querySelectorAll(".venue-profile-group[data-kind='family']").forEach((groupEl) => {
    updateFamilyToggleUi(groupEl, false);
  });
  document.querySelectorAll(".venue-profile-group[data-kind='item']").forEach((groupEl) => {
    updateItemToggleUi(groupEl, false);
  });

  syncActionUrls();
  syncInventoryExportUrls();
  applyInventoryView();
})();
