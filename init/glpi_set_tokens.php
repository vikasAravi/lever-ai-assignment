<?php
/**
 * Store API tokens encrypted with THIS GLPI instance's key.
 *
 * GLPI encrypts glpi_apiclients.app_token and glpi_users.api_token with
 * /var/glpi/config/glpicrypt.key, which every instance generates itself. A token
 * written by plain SQL can never match, so the pack sets tokens through GLPI's own
 * code: at seed-build time (temporary tokens) and at a candidate's first start
 * (their .env values), via the glpi-init service in docker-compose.yml.
 *
 * Env: GLPI_APP_TOKEN, GLPI_API_TOKEN_ONBOARDING, GLPI_API_TOKEN_INVENTORY,
 * optional GLPI_ADMIN_API_TOKEN. Prints only what it updated, never a value.
 */
chdir('/var/www/glpi');
require '/var/www/glpi/vendor/autoload.php';
$kernel = new \Glpi\Kernel\Kernel();
$kernel->boot();
if (!isset($DB) || !($DB instanceof DBmysql)) {
    // Decision: fall back to the legacy bootstrap if the kernel did not expose $DB.
    require '/var/www/glpi/inc/includes.php';
}
global $DB;
$key = new GLPIKey();
// Decision: a seeded instance never ran GLPI's installer, which is what normally
// creates /var/glpi/config/glpicrypt.key. Without it every encrypt() uses no key and
// no token can ever match. Create it here, once, readable by the web user.
if (!$key->keyExists()) {
    if (!$key->generate()) { fwrite(STDERR, "could not generate the security key\n"); exit(1); }
    $kf = GLPI_CONFIG_DIR . '/glpicrypt.key';
    @chown($kf, 'www-data'); @chgrp($kf, 'www-data'); @chmod($kf, 0640);
    echo "security key created\n";
}
if (getenv('KEY_ONLY')) { echo "key present\n"; exit(0); }
$enc = static fn (string $v): string => $key->encrypt($v);
$plain = static fn (string $name): string => (string) (getenv($name) ?: '');
foreach (['GLPI_APP_TOKEN', 'GLPI_API_TOKEN_ONBOARDING', 'GLPI_API_TOKEN_INVENTORY'] as $n) {
    if ($plain($n) === '') { fwrite(STDERR, "$n is not set\n"); exit(1); }
    if (!preg_match('/^[A-Za-z0-9]+$/', $plain($n))) { fwrite(STDERR, "$n must be [A-Za-z0-9]\n"); exit(1); }
}
$done = [];
$done[] = 'apiclient:' . (int) $DB->update('glpi_apiclients', ['app_token' => $enc($plain('GLPI_APP_TOKEN')), 'app_token_date' => $_SESSION['glpi_currenttime'] ?? date('Y-m-d H:i:s')], ['name' => 'assignment-pack']);
$done[] = 'onboarding.bot:' . (int) $DB->update('glpi_users', ['api_token' => $enc($plain('GLPI_API_TOKEN_ONBOARDING')), 'api_token_date' => date('Y-m-d H:i:s')], ['name' => 'onboarding.bot']);
$done[] = 'inventory.bot:' . (int) $DB->update('glpi_users', ['api_token' => $enc($plain('GLPI_API_TOKEN_INVENTORY')), 'api_token_date' => date('Y-m-d H:i:s')], ['name' => 'inventory.bot']);
if ($plain('GLPI_ADMIN_API_TOKEN') !== '') {
    $done[] = 'glpi:' . (int) $DB->update('glpi_users', ['api_token' => $enc($plain('GLPI_ADMIN_API_TOKEN')), 'api_token_date' => date('Y-m-d H:i:s')], ['name' => 'glpi']);
}
echo "tokens stored (" . implode(', ', $done) . ")\n";
