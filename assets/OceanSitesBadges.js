const BADGE_BASE = {
    display: 'inline-block', fontSize: '9px', fontWeight: '700',
    padding: '1px 6px', borderRadius: '3px', textTransform: 'uppercase',
    letterSpacing: '.04em', marginLeft: '2px', lineHeight: '1.5'
};
const BADGE_KIND = {
    changed: { border: '1px solid rgba(247,180,79,.4)', color: '#f7b44f', background: 'rgba(247,180,79,.08)' },
    added:   { border: '1px solid rgba(56,217,169,.4)', color: '#38d9a9', background: 'rgba(56,217,169,.08)' },
    removed: { border: '1px solid rgba(247,111,111,.4)', color: '#f76f6f', background: 'rgba(247,111,111,.08)' },
};
const HIDDEN = { display: 'none' };

function badge(kind) { return { ...BADGE_BASE, ...BADGE_KIND[kind] }; }

window.dash_clientside = Object.assign({}, window.dash_clientside, {
    oceanSites: {
        // Global fixed fields: only ever "changed"
        update_field_badges: function(diff) {
            if (!diff) { throw window.dash_clientside.PreventUpdate; }
            const changedByField = {};
            (diff.global_attributes.changed || []).forEach(e => { changedByField[e.field] = e; });

            const ids = window.dash_clientside.callback_context.outputs_list[0];
            const styles = [], texts = [];
            ids.forEach(m => {
                const e = changedByField[m.id.field];
                styles.push(e ? badge('changed') : HIDDEN);
                texts.push(e ? 'changed' : '');
            });
            return [styles, texts];
        },

        // Global extra attrs: added or changed
        update_extra_attr_badges: function(keyValues, diff, keyIds) {
            if (!diff) { throw window.dash_clientside.PreventUpdate; }
            const ga = diff.global_attributes;
            const added = new Set((ga.added || []).map(e => e.field.split('.').slice(1).join('.')));
            const changedKeys = new Set(
                (ga.changed || [])
                    .filter(e => e.field.startsWith('extra_global_attrs.'))
                    .map(e => e.field.split('.').slice(1).join('.'))
            );

            const keyByUid = {};
            keyIds.forEach((idObj, i) => { keyByUid[idObj.index] = (keyValues[i] || '').trim(); });

            const ids = window.dash_clientside.callback_context.outputs_list[0];
            const styles = [], texts = [];
            ids.forEach(m => {
                const key = keyByUid[m.id.index] || '';
                if (added.has(key)) { styles.push(badge('added')); texts.push('added'); }
                else if (changedKeys.has(key)) { styles.push(badge('changed')); texts.push('changed'); }
                else { styles.push(HIDDEN); texts.push(''); }
            });
            return [styles, texts];
        },

        // Variable fixed fields: only ever "changed"
        update_var_field_badges: function(diff, varsStore) {
            if (!diff) { throw window.dash_clientside.PreventUpdate; }
            const nameByUid = {};
            (varsStore || []).forEach(v => { nameByUid[v.uid] = v.name; });

            const changedByVar = {};
            (diff.variables.changed || []).forEach(entry => {
                const map = {};
                entry.fields.changed.forEach(f => { map[f.field] = f; });
                changedByVar[entry.name] = map;
            });

            const ids = window.dash_clientside.callback_context.outputs_list[0];
            const styles = [], texts = [];
            ids.forEach(m => {
                const name = nameByUid[m.id.index];
                const e = name ? changedByVar[name]?.[m.id.field] : null;
                styles.push(e ? badge('changed') : HIDDEN);
                texts.push(e ? 'changed' : '');
            });
            return [styles, texts];
        },

        // Variable extra attrs: added or changed
        update_var_extra_attr_badges: function(keyValues, diff, keyIds, varsStore) {
            if (!diff) { throw window.dash_clientside.PreventUpdate; }
            const nameByUid = {};
            (varsStore || []).forEach(v => { nameByUid[v.uid] = v.name; });

            const addedByVar = {}, changedByVar = {};
            (diff.variables.changed || []).forEach(entry => {
                addedByVar[entry.name] = new Set(
                    entry.fields.added.filter(f => f.field.startsWith('extra_attrs.'))
                        .map(f => f.field.split('.').slice(1).join('.'))
                );
                changedByVar[entry.name] = new Set(
                    entry.fields.changed.filter(f => f.field.startsWith('extra_attrs.'))
                        .map(f => f.field.split('.').slice(1).join('.'))
                );
            });

            const keyByCompositeKey = {};
            keyIds.forEach((idObj, i) => {
                keyByCompositeKey[`${idObj.index}::${idObj.attr_uid}`] = (keyValues[i] || '').trim();
            });

            const ids = window.dash_clientside.callback_context.outputs_list[0];
            const styles = [], texts = [];
            ids.forEach(m => {
                const name = nameByUid[m.id.index];
                const key = keyByCompositeKey[`${m.id.index}::${m.id.attr_uid}`] || '';
                if (name && addedByVar[name]?.has(key)) { styles.push(badge('added')); texts.push('added'); }
                else if (name && changedByVar[name]?.has(key)) { styles.push(badge('changed')); texts.push('changed'); }
                else { styles.push(HIDDEN); texts.push(''); }
            });
            return [styles, texts];
        }
    }
});
