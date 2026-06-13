// ===== Configuration Form Loading, Rendering, and Saving Logic =====

// Helper to add a dynamic preset prompt card
function addPromptItem(data) {
  data = data || { trigger: '', content: '' };
  var container = document.getElementById('prompts-list');
  var card = document.createElement('div');
  card.className = 'list-item-card';
  card.innerHTML = `
    <button class="remove-btn" onclick="this.parentElement.remove()">&times;</button>
    <div class="list-grid">
      <div class="form-group" style="grid-column: span 1">
        <label>触发词 / 触发词列表</label>
        <input type="text" class="text-input prompt-trigger" value="${data.trigger}" placeholder="例如: bnn 或 [词1,词2]">
      </div>
      <div class="form-group" style="grid-column: span 2">
        <label>提示词内容及参数</label>
        <textarea class="textarea-input prompt-content" style="min-height: 44px;" placeholder="例如: {{user_text}} --min_images 1">${data.content}</textarea>
      </div>
    </div>
  `;
  container.appendChild(card);
}

// Helper to add a dynamic provider priority card
function addProviderItem(selectedProvider) {
  selectedProvider = selectedProvider || '';
  var container = document.getElementById('providers-list');
  var card = document.createElement('div');
  card.className = 'list-item-card';
  
  var selectOptions = '<option value="">请选择提供商</option>';
  providers.forEach(function (p) {
    var selectedAttr = p.id === selectedProvider ? 'selected' : '';
    selectOptions += `<option value="${p.id}" ${selectedAttr}>${p.name}</option>`;
  });

  card.innerHTML = `
    <button class="remove-btn" onclick="this.parentElement.remove()">&times;</button>
    <div class="list-grid">
      <div class="form-group" style="grid-column: span 3">
        <label>模型供应商</label>
        <select class="select-input provider-select">${selectOptions}</select>
      </div>
    </div>
  `;
  container.appendChild(card);
}

// Helper to add a dynamic parameter alias mapping card
function addAliasItem(data) {
  data = data || { alias: '', target: '' };
  var container = document.getElementById('alias-list');
  var card = document.createElement('div');
  card.className = 'list-item-card';
  card.innerHTML = `
    <button class="remove-btn" onclick="this.parentElement.remove()">&times;</button>
    <div class="list-grid">
      <div class="form-group">
        <label>自定义参数名 (别名)</label>
        <input type="text" class="text-input alias-name" value="${data.alias}" placeholder="例如: append_mode">
      </div>
      <div class="form-group">
        <label>内置参数名称</label>
        <input type="text" class="text-input alias-target" value="${data.target}" placeholder="例如: gather_mode">
      </div>
    </div>
  `;
  container.appendChild(card);
}

// Helper to add a dynamic bot persona reference item card
function addPersonaRefItem(value) {
  value = value || '';
  var container = document.getElementById('bot-persona-refs-list');
  var card = document.createElement('div');
  card.className = 'list-item-card';
  card.innerHTML = `
    <button class="remove-btn" onclick="this.parentElement.remove()">&times;</button>
    <div class="list-grid">
      <div class="form-group" style="grid-column: span 3">
        <label>文件名或图片 URL</label>
        <input type="text" class="text-input persona-ref-value" value="${value}" placeholder="例如: bot_ref.jpg 或 https://example.com/bot.png">
      </div>
    </div>
  `;
  container.appendChild(card);
}

// Helper to add a dynamic whitelist item card (for users or groups)
function addWhitelistItem(containerId, value, placeholder) {
  value = value || '';
  var container = document.getElementById(containerId);
  var card = document.createElement('div');
  card.className = 'list-item-card';
  card.innerHTML = `
    <button class="remove-btn" onclick="this.parentElement.remove()">&times;</button>
    <div class="list-grid">
      <div class="form-group" style="grid-column: span 3">
        <label>标识 ID / UMO</label>
        <input type="text" class="text-input whitelist-value" value="${value}" placeholder="${placeholder}">
      </div>
    </div>
  `;
  container.appendChild(card);
}

// Helper to add a prefix list item card
function addPrefixItem(value) {
  value = value || '';
  var container = document.getElementById('prefix-list');
  var card = document.createElement('div');
  card.className = 'list-item-card';
  card.innerHTML = `
    <button class="remove-btn" onclick="this.parentElement.remove()">&times;</button>
    <div class="list-grid">
      <div class="form-group" style="grid-column: span 3">
        <label>命令前缀</label>
        <input type="text" class="text-input prefix-value" value="${value}" placeholder="例如: /">
      </div>
    </div>
  `;
  container.appendChild(card);
}

// Parse prompt settings from raw string representation
function parsePromptString(str) {
  str = str.trim();
  var spaceIdx = str.indexOf(' ');
  if (spaceIdx === -1) {
    return { trigger: str, content: '' };
  }
  return {
    trigger: str.substring(0, spaceIdx).trim(),
    content: str.substring(spaceIdx + 1).trim()
  };
}

// Load data from the backend APIs
function loadData() {
  document.getElementById('btnSave').disabled = true;
  var SDK = window.AstrBotPluginPage;
  if (!SDK) return;

  Promise.all([
    SDK.apiGet('config'),
    SDK.apiGet('providers')
  ]).then(function (results) {
    config = parseResponse(results[0]) || {};
    providers = parseResponse(results[1]) || [];

    // Populate providers list selects if any exists
    // Bind base checkboxes and inputs
    document.getElementById('stream').checked = !!config.stream;
    
    // Bind prompt_config nested object fields
    var pc = config.prompt_config || {};
    ['min_images', 'max_images'].forEach(function (k) {
      if (pc[k] !== undefined) document.getElementById('pc_' + k).value = pc[k];
    });
    document.getElementById('pc_aspect_ratio').value = pc.aspect_ratio || 'default';
    document.getElementById('pc_image_size').value = pc.image_size || '1K';
    document.getElementById('pc_google_search').checked = pc.google_search !== false;
    document.getElementById('pc_refer_images').value = pc.refer_images || '';
    document.getElementById('pc_gather_mode').checked = !!pc.gather_mode;

    // Bind common_config nested object fields
    var cc = config.common_config || {};
    document.getElementById('cc_preset_append').checked = cc.preset_append !== false;
    document.getElementById('cc_text_response').checked = !!cc.text_response;
    document.getElementById('cc_smart_retry').checked = cc.smart_retry !== false;
    if (cc.max_retry !== undefined) document.getElementById('cc_max_retry').value = cc.max_retry;
    if (cc.timeout !== undefined) document.getElementById('cc_timeout').value = cc.timeout;
    document.getElementById('cc_proxy').value = cc.proxy || '';

    // Bind image_hosting nested object fields
    var ih = config.image_hosting || {};
    document.getElementById('ih_enabled').checked = !!ih.enabled;
    document.getElementById('ih_upload_url').value = ih.upload_url || '';
    document.getElementById('ih_public_base_url').value = ih.public_base_url || '';
    document.getElementById('ih_auth_token').value = ih.auth_token || '';
    document.getElementById('ih_path_prefix').value = ih.path_prefix || 'big-banana';

    // Bind prefix_config nested object fields
    var pfx = config.prefix_config || {};
    document.getElementById('pfx_coexist_enabled').checked = !!pfx.coexist_enabled;

    // Bind vertex_ai_anonymous_config nested object fields
    var va = config.vertex_ai_anonymous_config || {};
    document.getElementById('va_recaptcha_base_api').value = va.recaptcha_base_api || 'https://www.google.com';
    document.getElementById('va_vertex_ai_anonymous_base_api').value = va.vertex_ai_anonymous_base_api || 'https://cloudconsole-pa.clients6.google.com';
    document.getElementById('va_system_prompt').value = va.system_prompt || '';
    if (va.max_retry !== undefined) document.getElementById('va_max_retry').value = va.max_retry;
    if (va.retry_delay !== undefined) document.getElementById('va_retry_delay').value = va.retry_delay;

    // Bind preference_config nested object fields
    var pref = config.preference_config || {};
    document.getElementById('pref_skip_at_first').checked = pref.skip_at_first !== false;
    document.getElementById('pref_skip_quote_first').checked = pref.skip_quote_first !== false;
    document.getElementById('pref_skip_llm_at_first').checked = pref.skip_llm_at_first !== false;
    document.getElementById('pref_drawing_message').value = pref.drawing_message || '🎨 在画了，请稍等一会...';

    // Bind llm_tool_settings nested object fields
    var tools = config.llm_tool_settings || {};
    document.getElementById('tools_llm_tool_enabled').checked = tools.llm_tool_enabled !== false;

    // Bind save_images nested object fields
    var saveImg = config.save_images || {};
    document.getElementById('save_local_save').checked = !!saveImg.local_save;

    // Bind whitelist_config nested object fields
    var wl = config.whitelist_config || {};
    document.getElementById('wl_enabled').checked = !!wl.enabled;
    document.getElementById('wl_user_enabled').checked = !!wl.user_enabled;

    // Render preset prompts list
    var promptsList = document.getElementById('prompts-list');
    promptsList.innerHTML = '';
    (config.prompt || []).forEach(function (item) {
      addPromptItem(parsePromptString(item));
    });

    // Render provider priority list
    var providersList = document.getElementById('providers-list');
    providersList.innerHTML = '';
    (config.image_generation_providers || []).forEach(function (prov) {
      addProviderItem(prov);
    });

    // Render parameter aliases list
    var aliasList = document.getElementById('alias-list');
    aliasList.innerHTML = '';
    (config.params_alias_map || []).forEach(function (mapping) {
      var parts = mapping.split(':');
      addAliasItem({ alias: parts[0] || '', target: parts[1] || '' });
    });

    // Render whitelists lists
    var groupWhitelistContainer = document.getElementById('group-whitelist-list');
    groupWhitelistContainer.innerHTML = '';
    (wl.whitelist || []).forEach(function (val) {
      addWhitelistItem('group-whitelist-list', val, '群组 UMO 标识');
    });

    var userWhitelistContainer = document.getElementById('user-whitelist-list');
    userWhitelistContainer.innerHTML = '';
    (wl.user_whitelist || []).forEach(function (val) {
      addWhitelistItem('user-whitelist-list', val, '用户 QQ 号');
    });

    // Render prefix lists
    var prefixListContainer = document.getElementById('prefix-list');
    prefixListContainer.innerHTML = '';
    (pfx.prefix_list || []).forEach(function (val) {
      addPrefixItem(val);
    });

    // Render bot persona reference images list
    var botPersonaRefsList = document.getElementById('bot-persona-refs-list');
    botPersonaRefsList.innerHTML = '';
    (pref.bot_persona_references || []).forEach(function (val) {
      addPersonaRefItem(val);
    });

    // Initialize all custom sliders UI display
    initSliders();
    document.getElementById('btnSave').disabled = false;
    showToast('配置数据加载成功');
  }).catch(function (error) {
    showToast('数据获取失败: ' + error.message);
  });
}

// Gather all inputs and save configuration
function saveAll() {
  var btnSave = document.getElementById('btnSave');
  btnSave.disabled = true;
  btnSave.textContent = '保存中...';
  var SDK = window.AstrBotPluginPage;
  if (!SDK) return;

  var updatedConfig = {};
  for (var key in config) {
    updatedConfig[key] = config[key];
  }

  // Update root values
  updatedConfig.stream = document.getElementById('stream').checked;

  // Build prompt_config object
  updatedConfig.prompt_config = {
    min_images: parseInt(document.getElementById('pc_min_images').value),
    max_images: parseInt(document.getElementById('pc_max_images').value),
    aspect_ratio: document.getElementById('pc_aspect_ratio').value,
    image_size: document.getElementById('pc_image_size').value,
    google_search: document.getElementById('pc_google_search').checked,
    refer_images: document.getElementById('pc_refer_images').value.trim(),
    gather_mode: document.getElementById('pc_gather_mode').checked
  };

  // Build common_config object
  updatedConfig.common_config = {
    preset_append: document.getElementById('cc_preset_append').checked,
    text_response: document.getElementById('cc_text_response').checked,
    smart_retry: document.getElementById('cc_smart_retry').checked,
    max_retry: parseInt(document.getElementById('cc_max_retry').value),
    timeout: parseFloat(document.getElementById('cc_timeout').value),
    proxy: document.getElementById('cc_proxy').value.trim()
  };

  // Build image_hosting object
  updatedConfig.image_hosting = {
    enabled: document.getElementById('ih_enabled').checked,
    upload_url: document.getElementById('ih_upload_url').value.trim(),
    public_base_url: document.getElementById('ih_public_base_url').value.trim(),
    auth_token: document.getElementById('ih_auth_token').value.trim(),
    path_prefix: document.getElementById('ih_path_prefix').value.trim()
  };

  // Build prefix_config object
  var prefixes = [];
  document.querySelectorAll('#prefix-list .prefix-value').forEach(function (input) {
    var val = input.value.trim();
    if (val) prefixes.push(val);
  });
  updatedConfig.prefix_config = {
    coexist_enabled: document.getElementById('pfx_coexist_enabled').checked,
    prefix_list: prefixes
  };

  // Build vertex_ai_anonymous_config object
  updatedConfig.vertex_ai_anonymous_config = {
    recaptcha_base_api: document.getElementById('va_recaptcha_base_api').value.trim(),
    vertex_ai_anonymous_base_api: document.getElementById('va_vertex_ai_anonymous_base_api').value.trim(),
    system_prompt: document.getElementById('va_system_prompt').value.trim(),
    max_retry: parseInt(document.getElementById('va_max_retry').value),
    retry_delay: parseFloat(document.getElementById('va_retry_delay').value)
  };

  // Build preference_config object
  var botPersonaRefs = [];
  document.querySelectorAll('#bot-persona-refs-list .persona-ref-value').forEach(function (input) {
    var val = input.value.trim();
    if (val) botPersonaRefs.push(val);
  });
  updatedConfig.preference_config = {
    skip_at_first: document.getElementById('pref_skip_at_first').checked,
    skip_quote_first: document.getElementById('pref_skip_quote_first').checked,
    skip_llm_at_first: document.getElementById('pref_skip_llm_at_first').checked,
    drawing_message: document.getElementById('pref_drawing_message').value.trim(),
    bot_persona_references: botPersonaRefs
  };

  // Build llm_tool_settings object
  updatedConfig.llm_tool_settings = {
    llm_tool_enabled: document.getElementById('tools_llm_tool_enabled').checked
  };

  // Build save_images object
  updatedConfig.save_images = {
    local_save: document.getElementById('save_local_save').checked
  };

  // Build whitelist_config object
  var groupWl = [];
  document.querySelectorAll('#group-whitelist-list .whitelist-value').forEach(function (input) {
    var val = input.value.trim();
    if (val) groupWl.push(val);
  });
  var userWl = [];
  document.querySelectorAll('#user-whitelist-list .whitelist-value').forEach(function (input) {
    var val = input.value.trim();
    if (val) userWl.push(val);
  });
  updatedConfig.whitelist_config = {
    enabled: document.getElementById('wl_enabled').checked,
    whitelist: groupWl,
    user_enabled: document.getElementById('wl_user_enabled').checked,
    user_whitelist: userWl
  };

  // Gather preset prompts list
  var promptList = [];
  document.querySelectorAll('#prompts-list .list-item-card').forEach(function (card) {
    var trigger = card.querySelector('.prompt-trigger').value.trim();
    var content = card.querySelector('.prompt-content').value.trim();
    if (trigger) {
      promptList.push(trigger + ' ' + content);
    }
  });
  updatedConfig.prompt = promptList;

  // Gather provider priority list
  var providersList = [];
  document.querySelectorAll('#providers-list .provider-select').forEach(function (select) {
    var val = select.value;
    if (val) providersList.push(val);
  });
  updatedConfig.image_generation_providers = providersList;

  // Gather parameter alias list
  var aliasList = [];
  document.querySelectorAll('#alias-list .list-item-card').forEach(function (card) {
    var name = card.querySelector('.alias-name').value.trim();
    var target = card.querySelector('.alias-target').value.trim();
    if (name && target) {
      aliasList.push(name + ':' + target);
    }
  });
  updatedConfig.params_alias_map = aliasList;

  // Save via endpoint
  SDK.apiPost('config', updatedConfig)
    .then(function (result) {
      if (result && result.status === 'ok') {
        config = updatedConfig;
        showToast('配置已保存并立即生效');
      } else {
        throw new Error(result ? result.message : '保存失败');
      }
    })
    .catch(function (err) {
      showToast('保存错误: ' + err.message);
    })
    .finally(function () {
      btnSave.disabled = false;
      btnSave.textContent = '💾 保存并生效';
    });
}
