/**
 * Fungal DNA Analysis Platform - free-mode intake (Google Apps Script, V8).
 *
 * What this script does
 *   setup()          one-off: creates the Google Form "Fungal DNA Analysis - submit data",
 *                    links its responses to the public ledger spreadsheet, makes sure the
 *                    `queue` tab exists with the right header, installs the onFormSubmit
 *                    trigger and stores the Form URLs in Script Properties.
 *   onFormSubmit(e)  trigger: maps one Form response to a `queue` row (status = queued)
 *                    and asks GitHub Actions to run it (repository_dispatch, event run-job).
 *                    Without a GitHub token the row is still queued; the workflow's
 *                    30-minute cron picks it up.
 *
 * Script Properties (File > Project settings > Script properties)
 *   LEDGER_SPREADSHEET_ID  default below (platform_config.json -> ledger.spreadsheet_id)
 *   GITHUB_REPO            "<github-user>/fungal-dna-platform"
 *   GITHUB_PAT             fine-grained personal access token, repository permission
 *                          "Contents: Read and write" on that repo only (needed for
 *                          POST /repos/{repo}/dispatches). Optional but recommended.
 *   FORM_ID, FORM_URL, FORM_EMBED_URL, FORM_EDIT_URL   written by setup()
 *
 * The Form file-upload question stores files in the owner's Google Drive
 * ("<form title> (File responses)" folder); file ids go to the queue row and the
 * GitHub Actions runner downloads them with the owner's OAuth token.
 *
 * Everything the platform publishes is public (CC BY 4.0) - the consent checkbox
 * in the Form is mandatory.
 */

// ---------------------------------------------------------------- constants
var DEFAULT_LEDGER_SPREADSHEET_ID = '1gpTf1k-i0DBJZDlUR1YQhiFgWCO56pjpSDa4M1cEJjA';
var FORM_TITLE = 'Fungal DNA Analysis - submit data';
var QUEUE_SHEET = 'queue';

// Exact column order of the `queue` tab (= platform_config.json ledger_headers.queue).
var QUEUE_HEADERS = [
  'job_id', 'job_type', 'submitted_at', 'status', 'submitter', 'submitter_email', 'marker',
  'fwd_primer', 'rev_primer', 'pairing', 'location', 'lat', 'lon', 'sequences_text',
  'file_ids', 'file_names', 'form_response_id', 'started_at', 'completed_at',
  'result_folder_url', 'result_page_url', 'error'
];

// Form item titles - onFormSubmit() looks items up by these exact strings.
var Q = {
  TYPE: 'Analysis type',
  MARKER: 'Marker / region',
  FWD: 'Forward primer (5\'-3\')',
  REV: 'Reverse primer (5\'-3\')',
  PAIRING: 'Read layout (amplicon only)',
  LOCATION: 'Sampling location',
  LAT: 'Latitude (decimal degrees)',
  LON: 'Longitude (decimal degrees)',
  SUBMITTER: 'Your name and affiliation',
  SEQUENCES: 'Paste sequences (DNA barcoding only)',
  FILES: 'Upload sequence files',
  CONSENT: 'Consent'
};

var TYPE_AMPLICON = 'Amplicon metabarcoding';
var TYPE_BARCODE = 'DNA barcoding';
var PRIMER_HELP =
  'Common pairs - ITS1: ITS1F CTTGGTCATTTAGAGGAAGTAA / ITS2 GCTGCGTTCTTCATCGATGC. ' +
  'ITS2: fITS7 GTGARTCATCGAATCTTTG / ITS4 TCCTCCGCTTATTGATATGC. ' +
  '18S-V4: TAReuk454FWD1 CCAGCASCYGCGGTAATTCC / TAReukREV3 ACTTTCGTTCTTGATYRA. ' +
  '18S-V9: 1389F TTGTACACACCGCCC / 1510R CCTTCYGCAGGTTCACCTAC. ' +
  'Leave both empty if your reads are already primer-trimmed.';

// Google Forms file-upload limits: at most 10 files per question, 1 GB per file (10 GB total).
var MAX_FILES = 10;
var MAX_FILE_MB = 1024;

// ---------------------------------------------------------------- setup
/**
 * Run once from the editor (Run > setup). Re-running is safe: an existing Form
 * (FORM_ID property) is reused, the trigger is re-installed only if missing.
 */
function setup() {
  var props = PropertiesService.getScriptProperties();
  var ssId = props.getProperty('LEDGER_SPREADSHEET_ID') || DEFAULT_LEDGER_SPREADSHEET_ID;
  var ss = SpreadsheetApp.openById(ssId);

  ensureQueueSheet_(ss);

  var form = null;
  var formId = props.getProperty('FORM_ID');
  if (formId) {
    try { form = FormApp.openById(formId); } catch (err) { form = null; }
  }
  if (!form) {
    form = createForm_();
    props.setProperty('FORM_ID', form.getId());
  }

  // Responses -> "Form Responses 1" tab of the ledger spreadsheet (needed for the trigger
  // event and as an audit trail; the `queue` tab is what the runner reads).
  var currentDest = null;
  try { currentDest = form.getDestinationId(); } catch (err) { currentDest = null; }
  if (currentDest !== ss.getId()) {
    form.setDestination(FormApp.DestinationType.SPREADSHEET, ss.getId());
  }

  installTrigger_(form);

  var urls = {
    FORM_URL: form.getPublishedUrl(),
    FORM_EMBED_URL: form.getPublishedUrl().replace(/\/viewform.*$/, '/viewform?embedded=true'),
    FORM_EDIT_URL: form.getEditUrl()
  };
  props.setProperties(urls);
  props.setProperty('LEDGER_SPREADSHEET_ID', ssId);

  Logger.log('Form created/linked: %s', FORM_TITLE);
  Logger.log('Public URL  : %s', urls.FORM_URL);
  Logger.log('Embed URL   : %s', urls.FORM_EMBED_URL);
  Logger.log('Edit URL    : %s', urls.FORM_EDIT_URL);
  Logger.log('Ledger      : https://docs.google.com/spreadsheets/d/%s', ssId);
  Logger.log('GITHUB_REPO : %s', props.getProperty('GITHUB_REPO') || '(not set - add it in Script properties)');
  Logger.log('GITHUB_PAT  : %s', props.getProperty('GITHUB_PAT') ? 'set' : '(not set - jobs will wait for the 30-min cron)');
  return urls;
}

/** Build the Form with all questions. */
function createForm_() {
  var form = FormApp.create(FORM_TITLE);
  form.setDescription(
    'Submit fungal sequence data for automated analysis (cutadapt + VSEARCH + UNITE). ' +
    'Amplicon metabarcoding: upload FASTQ(.gz) libraries (one marker per submission). ' +
    'DNA barcoding: upload FASTA / .ab1 traces or paste sequences. ' +
    'Results are automated and provisional; no negative controls are enforced; verify taxonomic ' +
    'assignments before publication. Everything you submit and every result is published openly (CC BY 4.0).');
  form.setCollectEmail(false);           // off: the ledger sheet is public, so no e-mail is stored
  form.setLimitOneResponsePerUser(false);
  form.setAllowResponseEdits(false);
  form.setConfirmationMessage(
    'Thank you - your job has been queued. Results appear on the public dashboard within a few minutes ' +
    'to a few hours depending on data size.');

  form.addMultipleChoiceItem()
    .setTitle(Q.TYPE)
    .setChoiceValues([TYPE_AMPLICON, TYPE_BARCODE])
    .setRequired(true);

  form.addMultipleChoiceItem()
    .setTitle(Q.MARKER)
    .setHelpText('Amplicon jobs: one marker per submission. Barcoding: the region your Sanger sequence covers.')
    .setChoiceValues(['ITS1', 'ITS2', '18S-V4', '18S-V9', 'other'])
    .setRequired(true);

  form.addTextItem()
    .setTitle(Q.FWD)
    .setHelpText(PRIMER_HELP)
    .setRequired(false);

  form.addTextItem()
    .setTitle(Q.REV)
    .setHelpText('Reverse primer as ordered (5\'-3\'). ' + PRIMER_HELP)
    .setRequired(false);

  form.addMultipleChoiceItem()
    .setTitle(Q.PAIRING)
    .setHelpText('paired-end: upload both R1 and R2 files (_1/_2 or _R1/_R2 in the names).')
    .setChoiceValues(['paired-end', 'single-end'])
    .setRequired(false);

  form.addTextItem().setTitle(Q.LOCATION)
    .setHelpText('e.g. "Bhitarkanika mangrove, Odisha"').setRequired(false);

  form.addTextItem().setTitle(Q.LAT)
    .setHelpText('e.g. 20.7 (north positive)')
    .setValidation(FormApp.createTextValidation().requireNumberBetween(-90, 90).build())
    .setRequired(false);

  form.addTextItem().setTitle(Q.LON)
    .setHelpText('e.g. 86.9 (east positive)')
    .setValidation(FormApp.createTextValidation().requireNumberBetween(-180, 180).build())
    .setRequired(false);

  form.addTextItem().setTitle(Q.SUBMITTER)
    .setHelpText('Shown publicly next to your results.')
    .setRequired(true);

  form.addParagraphTextItem()
    .setTitle(Q.SEQUENCES)
    .setHelpText('FASTA (with >headers) or bare sequences separated by blank lines. ' +
                 'Leave empty if you upload files.')
    .setRequired(false);

  // File upload: FASTQ / FASTA / .ab1. The documented FormApp API has no method to
  // create a file-upload question; some runtimes expose addFileUploadItem(), so we try
  // it and otherwise ask the owner to add the question once in the Form editor
  // (verifyForm() checks it). onFormSubmit() finds the item by type, not by title.
  addFileUploadQuestion_(form);

  var consent = form.addCheckboxItem()
    .setTitle(Q.CONSENT)
    .setChoiceValues(['I understand results are published openly under CC BY 4.0'])
    .setRequired(true);
  consent.setValidation(FormApp.createCheckboxValidation().requireSelectAtLeast(1).build());

  return form;
}


/**
 * Try to add the file-upload question programmatically; fall back to instructions.
 * Returns true when the question exists afterwards.
 */
function addFileUploadQuestion_(form) {
  if (hasFileUploadItem_(form)) { return true; }
  var helpText = 'FASTQ(.gz) libraries for amplicon jobs; FASTA or .ab1 traces for barcoding. ' +
    'Up to ' + MAX_FILES + ' files, ' + MAX_FILE_MB + ' MB each. ' +
    'Files are stored in the platform owner\'s Google Drive and published with the results.';
  if (typeof form.addFileUploadItem === 'function') {
    try {
      var upload = form.addFileUploadItem().setTitle(Q.FILES).setHelpText(helpText).setRequired(false);
      if (typeof upload.setMaxFiles === 'function') { upload.setMaxFiles(MAX_FILES); }
      if (typeof upload.setMaxFileSize === 'function') { upload.setMaxFileSize(MAX_FILE_MB * 1024 * 1024); }
      return true;
    } catch (err) {
      Logger.log('addFileUploadItem failed (%s); add the question manually.', err);
    }
  }
  // Placeholder so the owner sees where the question belongs; delete it after adding the real one.
  form.addSectionHeaderItem()
    .setTitle('>>> ACTION NEEDED: add a "File upload" question here titled "' + Q.FILES + '"')
    .setHelpText('Form editor: + Add question > File upload. Allow specific file types: OFF (FASTQ/.ab1 are not ' +
                 'in the preset list). Maximum number of files: ' + MAX_FILES + '. Maximum file size: 1 GB. ' +
                 'Then delete this placeholder and run verifyForm() in the script editor.');
  Logger.log('*** The file-upload question could not be created by script. Open the edit URL, add a ' +
             '"File upload" question titled "%s" (max %s files, 1 GB each), then run verifyForm().', Q.FILES, MAX_FILES);
  return false;
}

function hasFileUploadItem_(form) {
  var items = form.getItems();
  for (var i = 0; i < items.length; i++) {
    if (items[i].getType() === FormApp.ItemType.FILE_UPLOAD) { return true; }
  }
  return false;
}

/** Run after setup(): confirms the Form has a file-upload question, a destination and a trigger. */
function verifyForm() {
  var props = PropertiesService.getScriptProperties();
  var form = FormApp.openById(props.getProperty('FORM_ID'));
  var problems = [];
  if (!hasFileUploadItem_(form)) { problems.push('no File upload question (add it in the Form editor)'); }
  try { if (!form.getDestinationId()) { problems.push('no destination spreadsheet'); } }
  catch (err) { problems.push('no destination spreadsheet'); }
  var hasTrigger = ScriptApp.getProjectTriggers().some(function (t) {
    return t.getHandlerFunction() === 'onFormSubmit' && t.getTriggerSourceId() === form.getId();
  });
  if (!hasTrigger) { problems.push('onFormSubmit trigger missing (run setup())'); }
  if (!props.getProperty('GITHUB_PAT')) { problems.push('GITHUB_PAT not set (jobs run on the 30-min cron only)'); }
  if (!props.getProperty('GITHUB_REPO')) { problems.push('GITHUB_REPO not set'); }
  var items = form.getItems();
  for (var i = 0; i < items.length; i++) {
    if (items[i].getTitle().indexOf('ACTION NEEDED') === 0 && hasFileUploadItem_(form)) {
      form.deleteItem(items[i]);  // remove the placeholder once the real question exists
    }
  }
  Logger.log(problems.length ? 'Problems:\n- ' + problems.join('\n- ') : 'Form OK: ' + form.getPublishedUrl());
  return problems;
}

/** Make sure the `queue` tab exists and its header row matches QUEUE_HEADERS. */
function ensureQueueSheet_(ss) {
  var sh = ss.getSheetByName(QUEUE_SHEET);
  if (!sh) {
    sh = ss.insertSheet(QUEUE_SHEET);
  }
  var lastCol = sh.getLastColumn();
  var header = lastCol > 0 ? sh.getRange(1, 1, 1, lastCol).getValues()[0] : [];
  var same = header.length === QUEUE_HEADERS.length;
  for (var i = 0; same && i < QUEUE_HEADERS.length; i++) {
    same = String(header[i]).trim() === QUEUE_HEADERS[i];
  }
  if (!same) {
    if (sh.getLastRow() > 1) {
      throw new Error('`' + QUEUE_SHEET + '` tab has data but its header differs from QUEUE_HEADERS; fix it by hand.');
    }
    sh.clear();
    sh.getRange(1, 1, 1, QUEUE_HEADERS.length).setValues([QUEUE_HEADERS]).setFontWeight('bold');
    sh.setFrozenRows(1);
  }
  return sh;
}

/** Install the onFormSubmit trigger for the Form if it is not installed yet. */
function installTrigger_(form) {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    var t = triggers[i];
    if (t.getHandlerFunction() === 'onFormSubmit' && t.getTriggerSourceId() === form.getId()) {
      return;
    }
  }
  ScriptApp.newTrigger('onFormSubmit').forForm(form).onFormSubmit().create();
}

// ---------------------------------------------------------------- trigger
/**
 * Form-submit trigger (installable, forForm). `e.response` is the FormResponse.
 * Appends a `queue` row and dispatches the GitHub workflow.
 */
function onFormSubmit(e) {
  var response = e && e.response;
  if (!response) {
    throw new Error('onFormSubmit called without a FormResponse (install the trigger with setup()).');
  }
  var props = PropertiesService.getScriptProperties();
  var ssId = props.getProperty('LEDGER_SPREADSHEET_ID') || DEFAULT_LEDGER_SPREADSHEET_ID;
  var ss = SpreadsheetApp.openById(ssId);

  var row = responseToQueueRow_(response);
  appendQueueRow(ss, row);
  Logger.log('queued job %s (%s) from response %s', row.job_id, row.job_type, row.form_response_id);

  var dispatched = dispatchGitHub(row.job_id);
  if (!dispatched) {
    Logger.log('GitHub dispatch skipped/failed for %s - the scheduled workflow run will pick it up.', row.job_id);
  }
}

// Questions are matched by keywords in their titles (case-insensitive), so the owner can
// reword or regroup questions in the Form editor without breaking the mapping. First match wins.
var MATCH = {
  TYPE: [/analysis type/i],
  MARKER: [/marker/i, /region/i],
  PRIMER_PAIR: [/primer pair/i],
  FWD: [/forward primer/i],
  REV: [/reverse primer/i],
  PAIRING: [/read layout/i],
  LOCATION: [/sampling location/i, /^location/i],
  COORDS: [/coordinates/i],
  LAT: [/latitude/i],
  LON: [/longitude/i],
  SUBMITTER: [/name and affiliation/i, /your name/i],
  SEQUENCES: [/paste sequences/i, /sequences/i]
};

// Named primers -> 5'-3' sequences, for "Primer pair" answers that give names only.
var PRIMER_SEQS = {
  'ITS1F': 'CTTGGTCATTTAGAGGAAGTAA', 'ITS1': 'TCCGTAGGTGAACCTGCGG', 'ITS2': 'GCTGCGTTCTTCATCGATGC',
  'ITS3': 'GCATCGATGAAGAACGCAGC', 'ITS4': 'TCCTCCGCTTATTGATATGC', 'FITS7': 'GTGARTCATCGAATCTTTG',
  'ITS86F': 'GTGAATCATCGAATCTTTGAA', 'TAREUK454FWD1': 'CCAGCASCYGCGGTAATTCC',
  'TAREUKREV3': 'ACTTTCGTTCTTGATYRA', '1389F': 'TTGTACACACCGCCC', '1510R': 'CCTTCYGCAGGTTCACCTAC'
};

/** Answer of the first question whose title matches one of the patterns ('' if none). */
function pick_(answers, patterns) {
  var titles = Object.keys(answers);
  for (var p = 0; p < patterns.length; p++) {
    for (var t = 0; t < titles.length; t++) {
      if (patterns[p].test(titles[t]) && answers[titles[t]] !== '') { return answers[titles[t]]; }
    }
  }
  return '';
}

/** "ITS1F / ITS2", "CTTGG... / GCTGC...", "ITS1F (CTTGG...) / ITS2 (GCTGC...)" -> [fwd, rev]. */
function parsePrimerPair_(text) {
  var s = String(text || '');
  if (!s || /already|trimmed|none|no primer/i.test(s)) { return ['', '']; }
  var seqs = s.toUpperCase().match(/[ACGTRYSWKMBDHVN]{12,}/g) || [];
  if (seqs.length >= 2) { return [seqs[0], seqs[1]]; }
  var parts = s.split(/\s*(?:\/|\+|,|;|\band\b)\s*/i);
  var out = [];
  for (var i = 0; i < parts.length && out.length < 2; i++) {
    var key = parts[i].replace(/\(.*?\)/g, '').replace(/[^A-Za-z0-9]/g, '').toUpperCase();
    if (PRIMER_SEQS[key]) { out.push(PRIMER_SEQS[key]); }
  }
  return out.length === 2 ? out : ['', ''];
}

/** "20.7, 86.9" -> ['20.7', '86.9'] (only when both are valid decimal degrees). */
function parseCoords_(text) {
  var nums = String(text || '').match(/-?\d+(?:\.\d+)?/g) || [];
  if (nums.length < 2) { return ['', '']; }
  var lat = parseFloat(nums[0]), lon = parseFloat(nums[1]);
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) { return ['', '']; }
  return [String(lat), String(lon)];
}

/** Map a FormResponse to a queue row object keyed by QUEUE_HEADERS. */
function responseToQueueRow_(response) {
  var answers = {};
  var fileIds = [];
  var items = response.getItemResponses();
  for (var i = 0; i < items.length; i++) {
    var ir = items[i];
    var title = ir.getItem().getTitle();
    var val = ir.getResponse();
    if (ir.getItem().getType() === FormApp.ItemType.FILE_UPLOAD) {
      // response is an array of Drive file ids
      if (val && val.length) {
        for (var k = 0; k < val.length; k++) { fileIds.push(String(val[k])); }
      }
      continue;
    }
    answers[title] = Array.isArray(val) ? val.join('; ') : String(val === null || val === undefined ? '' : val);
  }

  var fileNames = [];
  for (var f = 0; f < fileIds.length; f++) {
    try {
      fileNames.push(DriveApp.getFileById(fileIds[f]).getName());
    } catch (err) {
      fileNames.push('');  // the runner falls back to files().get(name)
    }
  }

  var type = pick_(answers, MATCH.TYPE);
  var jobType = /barcod/i.test(type) ? 'barcode' : 'amplicon';
  var pairing = /single/i.test(pick_(answers, MATCH.PAIRING)) ? 'single-end' : 'paired-end';
  var primers = parsePrimerPair_(pick_(answers, MATCH.PRIMER_PAIR));
  var fwd = cleanPrimer_(pick_(answers, MATCH.FWD)) || primers[0];
  var rev = cleanPrimer_(pick_(answers, MATCH.REV)) || primers[1];
  var coords = parseCoords_(pick_(answers, MATCH.COORDS));
  var lat = pick_(answers, MATCH.LAT) || coords[0];
  var lon = pick_(answers, MATCH.LON) || coords[1];
  var marker = pick_(answers, MATCH.MARKER);
  var jobId = Utilities.getUuid().replace(/-/g, '').substring(0, 12);
  var submittedAt = response.getTimestamp() ? toIsoUtc_(response.getTimestamp()) : toIsoUtc_(new Date());
  var email = '';
  try { email = response.getRespondentEmail() || ''; } catch (err) { email = ''; }

  return {
    job_id: jobId,
    job_type: jobType,
    submitted_at: submittedAt,
    status: 'queued',
    submitter: pick_(answers, MATCH.SUBMITTER).substring(0, 200),
    submitter_email: email,
    marker: marker || (jobType === 'barcode' ? 'ITS' : 'ITS1'),
    fwd_primer: fwd,
    rev_primer: rev,
    pairing: pairing,
    location: pick_(answers, MATCH.LOCATION).substring(0, 200),
    lat: lat,
    lon: lon,
    sequences_text: jobType === 'barcode' ? pick_(answers, MATCH.SEQUENCES).substring(0, 45000) : '',
    file_ids: fileIds.join(','),
    file_names: fileNames.map(function (n) { return n.replace(/,/g, '_'); }).join(','),
    form_response_id: response.getId(),
    started_at: '',
    completed_at: '',
    result_folder_url: '',
    result_page_url: '',
    error: ''
  };
}

/** Append one row (object keyed by QUEUE_HEADERS) to the `queue` tab. */
function appendQueueRow(ss, row) {
  var sh = ensureQueueSheet_(ss);
  var values = QUEUE_HEADERS.map(function (h) {
    var v = row[h];
    return v === null || v === undefined ? '' : v;
  });
  // LockService: two near-simultaneous submissions must not interleave their appends.
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    sh.appendRow(values);
  } finally {
    lock.releaseLock();
  }
}

/**
 * POST https://api.github.com/repos/{GITHUB_REPO}/dispatches
 * body {event_type: 'run-job', client_payload: {job_id}}.
 * Returns true on HTTP 204, false if the token/repo is missing or the call failed.
 */
function dispatchGitHub(jobId) {
  var props = PropertiesService.getScriptProperties();
  var repo = (props.getProperty('GITHUB_REPO') || '').trim();
  var pat = (props.getProperty('GITHUB_PAT') || '').trim();
  if (!repo || !pat) {
    Logger.log('GITHUB_REPO / GITHUB_PAT not configured; job %s stays queued for the cron run.', jobId);
    return false;
  }
  var url = 'https://api.github.com/repos/' + repo + '/dispatches';
  var payload = { event_type: 'run-job', client_payload: { job_id: jobId } };
  var options = {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + pat,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28'
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };
  try {
    var resp = UrlFetchApp.fetch(url, options);
    var code = resp.getResponseCode();
    if (code === 204) {
      Logger.log('GitHub dispatch OK for job %s', jobId);
      return true;
    }
    Logger.log('GitHub dispatch failed for job %s: HTTP %s %s', jobId, code, resp.getContentText().substring(0, 300));
    return false;
  } catch (err) {
    Logger.log('GitHub dispatch error for job %s: %s', jobId, err);
    return false;
  }
}

// ---------------------------------------------------------------- helpers
function cleanPrimer_(s) {
  return String(s || '').replace(/[^A-Za-z]/g, '').toUpperCase();
}

function toIsoUtc_(d) {
  return Utilities.formatDate(d, 'UTC', "yyyy-MM-dd'T'HH:mm:ss'Z'");
}

/** Manual test helper: dispatch the workflow for an arbitrary job id (or an empty one = drain queue). */
function testDispatch() {
  Logger.log('dispatch result: %s', dispatchGitHub(''));
}

/**
 * Manual check after editing the Form: lists every question, the queue field it maps to,
 * and (for "Primer pair") the primers each choice resolves to. Unmapped questions are flagged.
 */
function checkMapping() {
  var form = FormApp.openById(PropertiesService.getScriptProperties().getProperty('FORM_ID'));
  var items = form.getItems();
  var lines = [];
  for (var i = 0; i < items.length; i++) {
    var it = items[i], title = it.getTitle(), type = it.getType();
    if (type === FormApp.ItemType.PAGE_BREAK || type === FormApp.ItemType.SECTION_HEADER) { continue; }
    var field = type === FormApp.ItemType.FILE_UPLOAD ? 'file_ids' : '';
    if (!field) {
      for (var key in MATCH) {
        if (MATCH[key].some(function (re) { return re.test(title); })) { field = key; break; }
      }
    }
    if (!field && /consent/i.test(title)) { field = '(consent)'; }
    lines.push((field || '!! NOT MAPPED') + '  <-  ' + title);
    if (type === FormApp.ItemType.MULTIPLE_CHOICE || type === FormApp.ItemType.LIST) {
      var choices = (type === FormApp.ItemType.MULTIPLE_CHOICE ? it.asMultipleChoiceItem() : it.asListItem()).getChoices();
      for (var c = 0; c < choices.length; c++) {
        var v = choices[c].getValue();
        var extra = field === 'PRIMER_PAIR' ? '  ->  ' + parsePrimerPair_(v).join(' / ') : '';
        lines.push('      - ' + v + extra);
      }
    }
  }
  Logger.log(lines.join('\n'));
}

/** Manual helper: print the stored Form URLs. */
function showUrls() {
  var p = PropertiesService.getScriptProperties().getProperties();
  Logger.log('FORM_URL=%s\nFORM_EMBED_URL=%s\nFORM_EDIT_URL=%s', p.FORM_URL, p.FORM_EMBED_URL, p.FORM_EDIT_URL);
}
