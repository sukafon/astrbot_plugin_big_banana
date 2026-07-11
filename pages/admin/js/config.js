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

// Helper to add a dynamic persona substitution rule card
function addPersonaReplaceItem(targetId, imgList) {
  targetId = targetId || '';
  imgList = imgList || [];
  var container = document.getElementById('persona-replace-list');
  var card = document.createElement('div');
  card.className = 'list-item-card persona-replace-card';
  
  // Generate a unique ID for this card to associate file inputs
  var cardId = 'persona_card_' + Math.random().toString(36).substr(2, 9);
  card.id = cardId;
  
  card.innerHTML = `
    <button class="remove-btn" onclick="this.parentElement.remove()">&times;</button>
    <div class="list-grid" style="grid-template-columns: 1fr;">
      <div class="form-group">
        <label>目标 ID / 别名 (例如: 1234567, bot, self)</label>
        <input type="text" class="text-input target-id-input" value="${targetId}" placeholder="输入 QQ 号或 bot / self">
      </div>
      <div class="form-group">
        <label>参考图片列表</label>
        <div class="images-sub-list" style="margin-top: 8px; display: flex; flex-wrap: wrap; gap: 12px;">
          <!-- Existing images go here -->
        </div>
        <div style="margin-top: 12px; display: flex; gap: 10px;">
          <button class="btn btn-secondary btn-sm" onclick="promptPersonaImageUrl('${cardId}')" type="button">＋ 添加图片 URL</button>
          <button class="btn btn-secondary btn-sm" onclick="triggerPersonaImageUpload('${cardId}')" type="button">＋ 上传本地图片</button>
          <input type="file" id="file_${cardId}" style="display: none;" accept="image/*" onchange="handlePersonaImageUpload(this, '${cardId}')">
        </div>
      </div>
    </div>
  `;
  container.appendChild(card);
  
  // Populate existing images
  imgList.forEach(function(url) {
    addPersonaImageRow(cardId, url);
  });
}

// Prompt the user for an image URL and add it
function promptPersonaImageUrl(cardId) {
  var url = prompt("请输入图片 URL:");
  if (url && url.trim()) {
    addPersonaImageRow(cardId, url.trim());
  }
}

// Helper to add a row to the image references inside a persona rule card
function addPersonaImageRow(cardId, url, overrideDisplayUrl) {
  if (!url) return;
  var card = document.getElementById(cardId);
  if (!card) return;
  var list = card.querySelector('.images-sub-list');
  
  var rowId = 'img_row_' + Math.random().toString(36).substr(2, 9);
  var row = document.createElement('div');
  row.className = 'image-row';
  row.id = rowId;
  row.style.position = 'relative';
  row.style.display = 'inline-block';
  row.style.width = '80px';
  row.style.height = '80px';
  row.style.borderRadius = '8px';
  row.style.overflow = 'hidden';
  row.style.border = '1px solid var(--input-border)';
  row.style.background = 'var(--input-bg)';
  row.style.boxShadow = '0 2px 8px rgba(0, 0, 0, 0.05)';
  
  var isUrl = url.startsWith('http://') || url.startsWith('https://');
  var displayUrl = overrideDisplayUrl || (isUrl ? url : '');
  
  row.innerHTML = `
    <img src="${displayUrl}" style="width: 100%; height: 100%; object-fit: cover;" alt="avatar" class="avatar-preview">
    <input type="hidden" class="image-url-input" value="${url}">
    <button class="remove-btn" onclick="this.parentElement.remove()" style="position: absolute; top: 4px; right: 4px; width: 18px; height: 18px; font-size: 10px; padding: 0; display: flex; align-items: center; justify-content: center; background: rgba(255, 59, 48, 0.85); color: white; border-radius: 50%; border: none; cursor: pointer; font-weight: bold; transition: background 0.2s;" type="button" onmouseover="this.style.background='rgba(255, 59, 48, 1)'" onmouseout="this.style.background='rgba(255, 59, 48, 0.85)'">&times;</button>
  `;
  list.appendChild(row);

  // If local file and no override URL is provided, fetch it asynchronously via API
  if (!isUrl && !overrideDisplayUrl) {
    var SDK = window.AstrBotPluginPage;
    if (SDK) {
      SDK.apiGet('image', { filename: url })
        .then(function (res) {
          var data = parseResponse(res);
          if (data && data.base64) {
            var img = row.querySelector('.avatar-preview');
            if (img) img.src = data.base64;
          }
        })
        .catch(function (err) {
          console.error('Failed to load local image preview:', err);
        });
    }
  }
}

// Trigger input click for file upload
function triggerPersonaImageUpload(cardId) {
  var fileInput = document.getElementById('file_' + cardId);
  if (fileInput) fileInput.click();
}

function handlePersonaImageUpload(fileInput, cardId) {
  var file = fileInput.files[0];
  if (!file) return;

  var SDK = window.AstrBotPluginPage;
  if (!SDK) {
    showToast('SDK not available');
    return;
  }

  showToast('正在读取文件...');
  var reader = new FileReader();
  reader.onload = function (e) {
    var base64Data = e.target.result;
    showToast('正在上传图片...');
    SDK.apiPost('upload_image', {
      filename: file.name,
      base64: base64Data
    })
      .then(function (res) {
        var data = parseResponse(res);
        if (data && data.filename) {
          addPersonaImageRow(cardId, data.filename, base64Data);
          showToast('图片上传成功');
        } else {
          throw new Error(res.message || '未知错误');
        }
      })
      .catch(function (err) {
        showToast('图片上传失败: ' + err.message);
      });
  };
  reader.onerror = function () {
    showToast('读取图片文件失败');
  };
  reader.readAsDataURL(file);

  // Clear input
  fileInput.value = '';
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
    SDK.apiGet('providers'),
    SDK.apiGet('substitutions')
  ]).then(function (results) {
    config = parseResponse(results[0]) || {};
    providers = parseResponse(results[1]) || [];
    var substitutions = parseResponse(results[2]) || {};

    // Populate sub_brain provider options dynamically
    var sbSelect = document.getElementById('sb_provider_id');
    if (sbSelect) {
      sbSelect.innerHTML = '<option value="">当前会话默认供应商</option>';
      providers.forEach(function (p) {
        var opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.name;
        sbSelect.appendChild(opt);
      });
    }

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
    document.getElementById('pc_moderation').value = pc.moderation || 'auto';

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

    // Bind sub_brain nested object fields
    var sb = config.sub_brain || {};
    document.getElementById('sb_enabled').checked = !!sb.enabled;
    document.getElementById('sb_provider_id').value = sb.provider_id || '';
    document.getElementById('sb_system_prompt').value = sb.system_prompt || '你是一个专业的视觉艺术大师与画图提示词优化助手。你的任务是将用户口语化的简短提示词，翻译并优化为适合 AI 生图（如 DALL-E 3、Stable Diffusion、Midjourney）的高质量英文提示词。\n\n请遵循以下优化规范丰富提示词：\n- Subject (主体): 补充动作、表情、衣着及材质等细节。\n- Style (艺术风格): 明确艺术风格（如 cyberpunk, anime illustration, photorealistic 等）。\n- Detail & Environment (环境): 补充背景及环境细节。\n- Lighting & Color (光影色彩): 设定光影色彩（如 cinematic lighting, golden hour 等）。\n- Quality Tags (画质标签): 加入画质修饰词（如 masterpiece, highly detailed, sharp focus 等）。\n\n特别要求：\n- 如果原始提示词中包含对参考图或头像编号的引用（如 "image 1", "image 2", "图1", "图2", "the character in image 1" 等），在翻译和优化时必须**完整且原样保留**这些引用标识（如 "the character in image 1", "image 1"），绝对不能用具体的角色名字替换它们或将它们删除。\n\n注意：直接输出优化后的最终英文提示词文本，绝对不要包含任何解释、问候或额外的 Markdown 格式（如包裹代码块的 ``` 或 "Prompt:" 等前缀）。';

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
    document.getElementById('pref_enable_drawing_message').checked = pref.enable_drawing_message !== false;
    document.getElementById('pref_drawing_message').value = pref.drawing_message || '🎨 在画了，请稍等一会...';
    document.getElementById('pref_group_cooldown').value = pref.group_cooldown || 0;

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
    document.getElementById('wl_only_for_commands').checked = !!wl.only_for_commands;

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

    // Render avatar substitutions list
    var personaReplaceList = document.getElementById('persona-replace-list');
    personaReplaceList.innerHTML = '';
    for (var targetId in substitutions) {
      if (substitutions.hasOwnProperty(targetId)) {
        addPersonaReplaceItem(targetId, substitutions[targetId]);
      }
    }

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
    gather_mode: document.getElementById('pc_gather_mode').checked,
    moderation: document.getElementById('pc_moderation').value
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

  // Build sub_brain object
  updatedConfig.sub_brain = {
    enabled: document.getElementById('sb_enabled').checked,
    provider_id: document.getElementById('sb_provider_id').value || '',
    system_prompt: document.getElementById('sb_system_prompt').value.trim()
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
  updatedConfig.preference_config = {
    skip_at_first: document.getElementById('pref_skip_at_first').checked,
    skip_quote_first: document.getElementById('pref_skip_quote_first').checked,
    skip_llm_at_first: document.getElementById('pref_skip_llm_at_first').checked,
    enable_drawing_message: document.getElementById('pref_enable_drawing_message').checked,
    drawing_message: document.getElementById('pref_drawing_message').value.trim(),
    group_cooldown: parseInt(document.getElementById('pref_group_cooldown').value) || 0
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
    user_whitelist: userWl,
    only_for_commands: document.getElementById('wl_only_for_commands').checked
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

  // Build substitutions mapping from persona replace list cards
  var substitutionsMap = {};
  document.querySelectorAll('#persona-replace-list .persona-replace-card').forEach(function (card) {
    var targetId = card.querySelector('.target-id-input').value.trim();
    if (!targetId) return;
    var imgUrls = [];
    card.querySelectorAll('.image-url-input').forEach(function (input) {
      var val = input.value.trim();
      if (val) imgUrls.push(val);
    });
    substitutionsMap[targetId] = imgUrls;
  });

  // Save via endpoint
  Promise.all([
    SDK.apiPost('config', updatedConfig),
    SDK.apiPost('substitutions', substitutionsMap)
  ])
    .then(function (results) {
      var configRes = results[0];
      var subsRes = results[1];
      if (configRes && configRes.status === 'ok' && subsRes && subsRes.status === 'ok') {
        config = updatedConfig;
        showToast('配置已保存并立即生效');
      } else {
        var errMsg = [];
        if (!configRes || configRes.status !== 'ok') errMsg.push(configRes ? configRes.message : '基本配置保存失败');
        if (!subsRes || subsRes.status !== 'ok') errMsg.push(subsRes ? subsRes.message : '人设替换保存失败');
        throw new Error(errMsg.join('; '));
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
