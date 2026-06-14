/**
 * 공통 유틸리티
 */

/** 숫자를 천단위 콤마 포맷으로 변환 */
function fmt(n) {
  if (n == null || n === '') return '0';
  return Math.round(n).toLocaleString('ko-KR');
}

/** JSON fetch 래퍼 */
async function fetchJSON(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const err = await res.text();
    console.error('API error:', res.status, err);
    return null;
  }
  return res.json();
}

/**
 * GridTable — 인라인 편집 그리드
 *
 * columns 정의:
 *   { key, type:'text|date|number|select|computed',
 *     options:[]/fn, compute:fn, render:fn, align:'end', step }
 */
class GridTable {
  constructor({ tableId, columns, apiUrl, getQueryParams, onLoad, getExtraData, onSave, onDelete, onStartEdit, selectable, onSelectChange }) {
    this.tableEl  = document.getElementById(tableId);
    this.tbody    = this.tableEl.querySelector('tbody');
    this.columns  = columns;
    this.apiUrl   = apiUrl;
    this.getQueryParams = getQueryParams || (() => '');
    this.onLoad         = onLoad         || null;
    this.getExtraData   = getExtraData   || (() => ({}));
    this.onSave         = onSave         || null;
    this.onDelete       = onDelete       || null;
    this.onStartEdit    = onStartEdit    || null;
    this.selectable       = selectable       || false;
    this.selected         = new Set();
    this.onSelectChange   = onSelectChange   || null;
    this.filters          = {}; // { key: Set(selected_values) }
    this._tr    = null;   // editing <tr>
    this._keyFn = null;
    this.rows   = [];
    this.visibleRows = [];
    this._ncols = columns.length + 1 + (this.selectable ? 1 : 0);

    // event delegation
    this.tbody.addEventListener('click', e => {
      // 체크박스 클릭은 편집 모드 진입하지 않음
      if (e.target.type === 'checkbox') {
        const tr = e.target.closest('tr[data-id]');
        if (tr) this._toggleSelect(tr.dataset.id, e.target.checked);
        return;
      }
      const btn = e.target.closest('[data-ga]');
      if (btn) {
        e.stopPropagation();
        const a = btn.dataset.ga;
        if      (a === 's') this.saveEdit();
        else if (a === 'c') this.cancelEdit();
        else if (a === 'd') this._delete(btn.dataset.id);
        return;
      }
      const tr = e.target.closest('tr[data-id]');
      if (tr && tr !== this._tr) this.startEdit(tr);
    });

    // 헤더 필터 이벤트
    this.tableEl.querySelector('thead').addEventListener('click', e => {
      const th = e.target.closest('.th-filter');
      if (th) {
        e.stopPropagation();
        this._toggleFilterPopup(th);
      }
    });

    // click outside → cancel / close popup
    this._ignoreDocClick = false;
    document.addEventListener('click', e => {
      if (this._ignoreDocClick) return;
      if (this._tr && !this.tableEl.contains(e.target)) this.cancelEdit();
      
      // 필터 팝업 닫기
      if (!e.target.closest('.filter-popup') && !e.target.closest('.th-filter')) {
        this.tableEl.querySelectorAll('.filter-popup').forEach(p => p.classList.remove('show'));
      }
    });
  }

  async load() {
    const qs   = this.getQueryParams();
    const data = await fetchJSON(this.apiUrl + (qs ? '?' + qs : ''));
    if (Array.isArray(data)) {
      this.rows = data;
      this.meta = {};
    } else {
      this.rows = data?.rows || [];
      this.meta = data || {};
    }
    this._renderAll();
    this.onLoad?.(this.rows, this.meta);
  }

  _renderAll() {
    this._tr = null;
    if (this.selectable) this.selected.clear();
    
    // 필터링 적용
    this.visibleRows = this.rows.filter(r => {
      for (const key in this.filters) {
        const selected = this.filters[key];
        if (selected.size > 0) {
          const col = this.columns.find(c => c.key === key);
          let val = (col?.type === 'computed' ? col.compute(r) : r[key]) ?? '';
          if (col?.render && col.type !== 'computed') val = col.render(val, r);
          // HTML 태그 제거 후 비교
          const cleanVal = String(val).replace(/<[^>]*>/g, '').trim() || '-';
          if (!selected.has(cleanVal)) return false;
        }
      }
      return true;
    });

    this.tbody.innerHTML = this.visibleRows.length
      ? this.visibleRows.map(r => this._viewHtml(r)).join('')
      : `<tr><td colspan="${this._ncols}" class="text-center text-muted py-4">데이터가 없습니다.</td></tr>`;
    
    if (this.selectable) this._updateSelectAll();
    
    // 필터링된 결과로 요약 정보 갱신 (onLoad 재호출 효과)
    if (this.rows.length !== this.visibleRows.length) {
      const filteredMeta = { ...this.meta };
      filteredMeta.total = this.visibleRows.reduce((sum, r) => sum + (r.amount || 0), 0);
      this.onLoad?.(this.visibleRows, filteredMeta);
    }
    this._updateFilterIcons();
  }

  _updateFilterIcons() {
    this.tableEl.querySelectorAll('.th-filter').forEach(th => {
      const key = th.dataset.key;
      const icon = th.querySelector('.filter-icon');
      if (this.filters[key] && this.filters[key].size > 0) icon.classList.add('active');
      else icon.classList.remove('active');
    });
  }

  _toggleFilterPopup(th) {
    const key = th.dataset.key;
    let popup = th.querySelector('.filter-popup');
    
    if (!popup) {
      popup = document.createElement('div');
      popup.className = 'filter-popup';
      th.appendChild(popup);
    }
    
    const isShowing = popup.classList.contains('show');
    this.tableEl.querySelectorAll('.filter-popup').forEach(p => p.classList.remove('show'));
    if (isShowing) return;

    // 데이터 추출
    const col = this.columns.find(c => c.key === key);
    const allUnique = [...new Set(this.rows.map(r => {
      let v = (col.type === 'computed' ? col.compute(r) : r[key]) ?? '';
      if (col.render && col.type !== 'computed') v = col.render(v, r);
      return String(v).replace(/<[^>]*>/g, '').trim() || '-';
    }))].sort();

    const selected = this.filters[key] || new Set();

    popup.innerHTML = `
      <div class="filter-header">
        <input type="text" class="form-control form-control-sm filter-search" placeholder="검색...">
      </div>
      <div class="filter-body">
        <label class="filter-item border-bottom mb-1">
          <input type="checkbox" class="filter-all" ${selected.size === 0 ? 'checked' : ''}> <span>(전체 선택)</span>
        </label>
        ${allUnique.map(v => `
          <label class="filter-item">
            <input type="checkbox" class="filter-opt" value="${v}" ${selected.has(v) || selected.size === 0 ? 'checked' : ''}>
            <span>${v}</span>
          </label>
        `).join('')}
      </div>
      <div class="filter-footer">
        <button class="btn btn-sm btn-outline-secondary filter-clear" style="font-size:0.7rem">초기화</button>
        <button class="btn btn-sm btn-primary filter-apply" style="font-size:0.7rem">적용</button>
      </div>
    `;

    popup.classList.add('show');

    // 팝업 내부 클릭이 밖으로 퍼져서 팝업이 닫히거나 다른 이벤트가 발생하는 것을 방지
    popup.addEventListener('click', e => e.stopPropagation());

    // 내부 이벤트
    const body = popup.querySelector('.filter-body');
    const search = popup.querySelector('.filter-search');
    search.focus();
    search.addEventListener('input', () => {
      const q = search.value.toLowerCase();
      body.querySelectorAll('.filter-item').forEach(item => {
        if (item.classList.contains('border-bottom')) return;
        const txt = item.textContent.toLowerCase();
        item.style.display = txt.includes(q) ? 'flex' : 'none';
      });
    });

    popup.querySelector('.filter-all').addEventListener('change', e => {
      body.querySelectorAll('.filter-opt').forEach(cb => cb.checked = e.target.checked);
    });

    popup.querySelector('.filter-clear').addEventListener('click', () => {
      delete this.filters[key];
      popup.classList.remove('show');
      this._renderAll();
    });

    popup.querySelector('.filter-apply').addEventListener('click', () => {
      const checked = [...body.querySelectorAll('.filter-opt:checked')].map(cb => cb.value);
      if (checked.length === allUnique.length) {
        delete this.filters[key];
      } else {
        this.filters[key] = new Set(checked);
      }
      popup.classList.remove('show');
      this._renderAll();
    });
  }

  resetFilters() {
    this.filters = {};
    this._renderAll();
  }

  _toggleSelect(id, checked) {
    if (checked) this.selected.add(String(id));
    else         this.selected.delete(String(id));
    this._updateSelectAll();
    this.onSelectChange?.(this.selected);
  }

  _updateSelectAll() {
    const cbAll = this.tableEl.querySelector('.cb-all');
    if (!cbAll) return;
    const allIds = this.rows.map(r => String(r.id));
    cbAll.checked = allIds.length > 0 && allIds.every(id => this.selected.has(id));
    cbAll.indeterminate = !cbAll.checked && this.selected.size > 0;
  }

  selectAll(checked) {
    this.rows.forEach(r => {
      const cb = this.tbody.querySelector(`tr[data-id="${r.id}"] .row-cb`);
      if (cb) cb.checked = checked;
      if (checked) this.selected.add(String(r.id));
      else         this.selected.delete(String(r.id));
    });
    this._updateSelectAll();
    this.onSelectChange?.(this.selected);
  }

  _viewHtml(r) {
    const cells = this.columns.map(col => {
      const cls = col.align === 'end' ? ' class="text-end"' : '';
      let content;
      if (col.type === 'computed') {
        content = col.compute(r) ?? '';
      } else {
        const v = r[col.key] ?? '';
        content = col.render ? col.render(v, r) : v;
      }
      return `<td${cls}>${content}</td>`;
    });
    cells.push(`<td class="text-center"><button class="btn btn-sm btn-outline-danger py-0" data-ga="d" data-id="${r.id}"><i class="bi bi-trash"></i></button></td>`);
    const cbCell = this.selectable
      ? `<td class="text-center"><input type="checkbox" class="form-check-input row-cb" value="${r.id}"${this.selected.has(String(r.id)) ? ' checked' : ''}></td>`
      : '';
    return `<tr data-id="${r.id}" class="grid-row">${cbCell}${cells.join('')}</tr>`;
  }

  _editInner(r) {
    const cells = this.columns.map(col => {
      if (col.type === 'computed') {
        const cls = col.align === 'end' ? ' class="text-end"' : '';
        return `<td${cls}>${col.compute(r) ?? ''}</td>`;
      }
      const raw = r[col.key] ?? '';
      const v = (col.type === 'date' && raw === '')
        ? new Date().toISOString().split('T')[0]
        : raw;
      let inp;
      if (col.type === 'select') {
        const opts = (typeof col.options === 'function' ? col.options() : col.options || [])
          .map(o => {
            const ov = typeof o === 'object' ? o.value : o;
            const ol = typeof o === 'object' ? o.label : o;
            return `<option value="${ov}"${String(ov) === String(v) ? ' selected' : ''}>${ol}</option>`;
          }).join('');
        inp = `<select class="form-select form-select-sm" data-key="${col.key}"><option value=""></option>${opts}</select>`;
      } else if (col.type === 'number') {
        const fmtd = (v !== '' && v != null && !isNaN(v))
          ? Number(v).toLocaleString('ko-KR') : '';
        inp = `<input type="text" inputmode="decimal" class="form-control form-control-sm" data-key="${col.key}" data-numeric="true" value="${fmtd}">`;
      } else {
        const t = {text:'text', date:'date'}[col.type] || 'text';
        inp = `<input type="${t}" class="form-control form-control-sm" data-key="${col.key}" value="${v}">`;
      }
      const cls = col.align === 'end' ? ' class="text-end"' : '';
      return `<td${cls}>${inp}</td>`;
    });
    
    // 동작 버튼 열
    cells.push(`<td class="text-center" style="white-space:nowrap">
      <button class="btn btn-sm btn-success py-0 me-1" data-ga="s"><i class="bi bi-check-lg"></i></button>
      <button class="btn btn-sm btn-outline-secondary py-0" data-ga="c"><i class="bi bi-x-lg"></i></button>
    </td>`);
    
    // 체크박스 열 유지 (선택은 불가능하게 disabled)
    const cbCell = this.selectable
      ? `<td class="text-center"><input type="checkbox" class="form-check-input" disabled ${this.selected.has(String(r.id)) ? 'checked' : ''}></td>`
      : '';
      
    return `${cbCell}${cells.join('')}`;
  }

  startEdit(tr) {
    if (this._tr) this.cancelEdit();
    this._tr = tr;
    const id = tr.dataset.id;
    const r  = id === 'new' ? {} : (this.rows.find(x => String(x.id) === id) || {});
    tr.innerHTML = this._editInner(r);
    tr.classList.add('grid-editing');
    tr.querySelectorAll('input[data-numeric]').forEach(el => {
      el.addEventListener('input', () => {
        const sel  = el.selectionStart;
        const prev = el.value;
        const clean = prev.replace(/[^\d.]/g, '');
        const parts = clean.split('.');
        const intFmt = (parts[0] || '').replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        const next = parts.length > 1 ? intFmt + '.' + parts[1] : intFmt;
        el.value = next;
        try { el.setSelectionRange(sel + next.length - prev.length, sel + next.length - prev.length); } catch {}
      });
    });
    this.onStartEdit?.(tr, r);
    tr.querySelector('input,select')?.focus();
    tr.addEventListener('keydown', this._keyFn = e => {
      // 엔터 누르면 저장 (모든 입력창에서 허용)
      if (e.key === 'Enter') { 
        e.preventDefault(); 
        this.saveEdit(); 
      }
      if (e.key === 'Escape') this.cancelEdit();
    });
    // 이 클릭 이벤트가 document까지 버블링되어 즉시 cancelEdit 되는 것을 방지
    this._ignoreDocClick = true;
    setTimeout(() => { this._ignoreDocClick = false; }, 0);
  }

  cancelEdit() {
    if (!this._tr) return;
    const tr = this._tr;
    tr.removeEventListener('keydown', this._keyFn);
    this._tr = null;
    if (tr.dataset.id === 'new') {
      tr.remove();
    } else {
      const r = this.rows.find(x => String(x.id) === tr.dataset.id);
      if (r) tr.outerHTML = this._viewHtml(r);
    }
  }

  async saveEdit() {
    if (!this._tr) return;
    const tr = this._tr;
    const id = tr.dataset.id;
    const data = { ...this.getExtraData() };
    tr.querySelectorAll('[data-key]').forEach(el => {
      const col = this.columns.find(c => c.key === el.dataset.key);
      data[el.dataset.key] = col?.type === 'number' ? (parseFloat(el.value.replace(/,/g, '')) || 0) : el.value;
    });
    tr.removeEventListener('keydown', this._keyFn);
    this._tr = null;
    const method = id === 'new' ? 'POST' : 'PUT';
    const url    = id === 'new' ? this.apiUrl : `${this.apiUrl}/${id}`;
    await fetchJSON(url, { method, headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) });
    await this.load();
    this.onSave?.();
  }

  async _delete(id) {
    if (this._tr) this.cancelEdit();
    if (!confirm('삭제하시겠습니까?')) return;
    await fetchJSON(`${this.apiUrl}/${id}`, { method: 'DELETE' });
    await this.load();
    this.onDelete?.();
  }

  addRow() {
    if (this._tr) this.cancelEdit();
    const tr = document.createElement('tr');
    tr.dataset.id = 'new';
    this.tbody.insertBefore(tr, this.tbody.firstChild);
    this.startEdit(tr);
  }
}

/** 년도/월 셀렉트 초기화 */
function initYearMonthFilters(yearId, monthId, defaultYear, defaultMonth) {
  const yearSel  = document.getElementById(yearId);
  const monthSel = document.getElementById(monthId);
  const curYear  = new Date().getFullYear();

  for (let y = curYear; y >= curYear - 5; y--) {
    const opt = document.createElement('option');
    opt.value = y;
    opt.textContent = y + '년';
    if (y === defaultYear) opt.selected = true;
    yearSel.appendChild(opt);
  }

  for (let m = 1; m <= 12; m++) {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = m + '월';
    if (m === defaultMonth) opt.selected = true;
    monthSel.appendChild(opt);
  }
}
