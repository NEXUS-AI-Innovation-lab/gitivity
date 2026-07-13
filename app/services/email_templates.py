"""Email template builders for the approval workflow.

Pure functions (no I/O) ported from the former n8n approval workflow
("Build Approver Email" and "Build Confirmation Email" Code nodes).
"""

OPERATION_LABELS = {
    "CREATE_USER": "Creation",
    "UPDATE_USER": "Modification",
    "DELETE_USER": "Suppression",
}

SERVICE_LABELS = {
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "ldap": "LDAP",
    "odoo": "Odoo",
    "mongodb": "MongoDB",
}

GENERIC_ROLES = {"readonly", "readwrite", "read", "write", "admin", "superuser"}


def filter_roles_for_service(roles: list[str], target_service: str) -> list[str]:
    """Keep roles relevant to the target service, plus generic roles.

    Falls back to the full list when no role matches.
    """
    target_lower = target_service.lower()
    relevant = [
        r for r in roles
        if target_lower in r.lower() or r.lower() in GENERIC_ROLES
    ]
    return relevant if relevant else roles


def compute_warnings(
    operation_type: str,
    user_data: dict,
    changes: list[dict] | None = None,
) -> list[str]:
    """Automatic risk analysis on the provisioning request (former "Analyse IA")."""
    attrs = user_data.get("attributes") or {}
    roles = user_data.get("roles") or []
    description = (attrs.get("description") or "").lower()
    title = (attrs.get("title") or "").lower()

    warnings: list[str] = []

    has_admin_role = any("admin" in r.lower() for r in roles)

    if any(kw in description or kw in title for kw in ("stagiaire", "intern")) and has_admin_role:
        warnings.append("Utilisateur stagiaire/intern avec role admin detecte")

    admin_roles = [r for r in roles if "admin" in r.lower()]
    if len(admin_roles) > 1:
        warnings.append("Roles admin sur plusieurs services : " + ", ".join(admin_roles))

    if operation_type == "DELETE_USER" and has_admin_role:
        warnings.append("Suppression d'un compte avec privileges admin")

    if operation_type == "CREATE_USER" and not user_data.get("email"):
        warnings.append("Creation de compte sans adresse email")

    if not description and not title:
        warnings.append("Aucune description ou poste renseigne pour cet utilisateur")

    if len(roles) > 4:
        warnings.append(f"Nombre eleve de roles ({len(roles)}) - verifier la necessite")

    if operation_type == "UPDATE_USER":
        if not changes:
            warnings.append("Modification sans changement visible detecte")
        else:
            if any(c.get("field") == "Mot de passe" for c in changes):
                warnings.append("Changement de mot de passe - verifier l'identite du demandeur")
            if any(c.get("field") == "Roles" for c in changes) and has_admin_role:
                warnings.append("Modification de roles avec privileges admin")

    return warnings


def _changes_table_html(changes: list[dict] | None) -> str:
    """Before/after table for UPDATE operations."""
    if not changes:
        return (
            '<div style="background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; '
            'padding: 15px; margin: 20px 0; color: #856404;">'
            "<strong>Aucun detail de modification disponible.</strong></div>"
        )

    rows = ""
    for c in changes:
        detail_line = ""
        if c.get("details"):
            detail_line = f'<br><small style="color: #666;">{c["details"]}</small>'
        rows += (
            "<tr>"
            f'<td style="padding: 8px 12px; font-weight: bold; color: #555; border-bottom: 1px solid #eee;">{c.get("field", "")}</td>'
            f'<td style="padding: 8px 12px; background: #ffebee; color: #c62828; border-bottom: 1px solid #eee;"><del>{c.get("old") or "Aucun"}</del></td>'
            f'<td style="padding: 8px 12px; background: #e8f5e9; color: #2e7d32; border-bottom: 1px solid #eee;"><strong>{c.get("new") or "Aucun"}</strong>{detail_line}</td>'
            "</tr>"
        )

    return (
        '<div style="background: #e8f5e9; border: 1px solid #4caf50; border-radius: 6px; '
        'padding: 15px; margin: 20px 0;">'
        '<strong style="color: #2e7d32;">Modifications apportees :</strong>'
        '<table style="width: 100%; border-collapse: collapse; margin-top: 10px;">'
        '<tr style="background: #f5f5f5;">'
        '<th style="padding: 8px 12px; text-align: left; border-bottom: 2px solid #ddd;">Champ</th>'
        '<th style="padding: 8px 12px; text-align: left; border-bottom: 2px solid #ddd;">Avant</th>'
        '<th style="padding: 8px 12px; text-align: left; border-bottom: 2px solid #ddd;">Apres</th>'
        "</tr>"
        f"{rows}</table></div>"
    )


def build_approval_email(
    operation_id: str,
    operation_type: str,
    target_service: str,
    user_data: dict,
    approver_name: str,
    approver_level: int,
    total_approvers: int,
    approve_url: str,
    reject_url: str,
    changes: list[dict] | None = None,
) -> tuple[str, str]:
    """Build the approval request email.

    Returns:
        (subject, html_body)
    """
    attrs = user_data.get("attributes") or {}
    username = user_data.get("username") or "N/A"
    email = user_data.get("email") or "N/A"
    first_name = user_data.get("first_name") or "N/A"
    last_name = user_data.get("last_name") or "N/A"
    description = attrs.get("description") or ""
    title = attrs.get("title") or ""

    operation_label = OPERATION_LABELS.get(operation_type, operation_type)

    display_roles = filter_roles_for_service(user_data.get("roles") or [], target_service)
    roles_display = (
        "<br>".join("• " + r for r in display_roles) if display_roles else "Aucun"
    )

    warnings = compute_warnings(operation_type, user_data, changes)

    description_row = f"<tr><td>Description</td><td>{description}</td></tr>" if description else ""
    title_row = f"<tr><td>Poste</td><td>{title}</td></tr>" if title else ""

    details_html = (
        '<table class="info-table">'
        f"<tr><td>Operation</td><td><strong>{operation_label}</strong></td></tr>"
        f"<tr><td>Utilisateur</td><td><strong>{username}</strong></td></tr>"
        f"<tr><td>Email</td><td>{email}</td></tr>"
        f"<tr><td>Prenom</td><td>{first_name}</td></tr>"
        f"<tr><td>Nom</td><td>{last_name}</td></tr>"
        f"{description_row}"
        f"{title_row}"
        f"<tr><td>Service cible</td><td><strong>{target_service}</strong></td></tr>"
        f"<tr><td>Roles</td><td>{roles_display}</td></tr>"
        f'<tr><td>Operation ID</td><td><code style="font-size: 11px; color: #888;">{operation_id}</code></td></tr>'
        "</table>"
    )

    if operation_type == "UPDATE_USER":
        details_html += _changes_table_html(changes)

    warnings_html = ""
    if warnings:
        warning_items = "".join(f'<li style="margin: 5px 0;">{w}</li>' for w in warnings)
        warnings_html = (
            '<div style="background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; '
            'padding: 15px; margin: 20px 0; color: #856404;">'
            "<strong>Analyse automatique :</strong>"
            f'<ul style="margin: 10px 0; padding-left: 20px;">{warning_items}</ul></div>'
        )

    level_info = ""
    if total_approvers > 1:
        prev_text = (
            "Les niveaux precedents ont approuve cette demande."
            if approver_level > 1
            else "Vous etes le premier approbateur dans la chaine de validation."
        )
        level_info = (
            '<div style="background: #e3f2fd; border: 1px solid #2196f3; border-radius: 6px; '
            'padding: 15px; margin: 20px 0; color: #1565c0;">'
            f"<strong>Approbation niveau {approver_level}/{total_approvers}</strong><br>{prev_text}</div>"
        )

    html_body = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
        "body { font-family: Arial, sans-serif; background: #f4f4f4; margin: 0; padding: 20px; }"
        ".container { max-width: 700px; margin: 0 auto; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }"
        ".header { background: #2c3e50; color: white; padding: 20px 30px; }"
        ".header h1 { margin: 0; font-size: 20px; }"
        ".content { padding: 30px; }"
        ".info-table { width: 100%; border-collapse: collapse; margin: 10px 0; }"
        ".info-table td { padding: 10px 15px; border-bottom: 1px solid #eee; }"
        ".info-table td:first-child { font-weight: bold; color: #555; width: 140px; }"
        ".buttons { text-align: center; margin: 30px 0; }"
        ".btn { display: inline-block; padding: 14px 40px; margin: 0 10px; border-radius: 6px; text-decoration: none; font-weight: bold; font-size: 16px; }"
        ".btn-approve { background: #27ae60; color: white; }"
        ".btn-reject { background: #e74c3c; color: white; }"
        ".footer { background: #f8f9fa; padding: 15px 30px; text-align: center; color: #888; font-size: 12px; }"
        '</style></head><body><div class="container">'
        f'<div class="header"><h1>Gateway IAM - Demande d\'approbation (Niveau {approver_level})</h1></div>'
        '<div class="content">'
        f"<p>Bonjour <strong>{approver_name}</strong>,</p>"
        "<p>Une demande de provisioning necessite votre approbation :</p>"
        f"{level_info}{details_html}{warnings_html}"
        '<div class="buttons">'
        f'<a href="{approve_url}" class="btn btn-approve">APPROUVER</a>'
        f'<a href="{reject_url}" class="btn btn-reject">REJETER</a>'
        "</div></div>"
        '<div class="footer">Gateway IAM Provisioning System</div>'
        "</div></body></html>"
    )

    subject = (
        f"[Gateway IAM] Approbation Niveau {approver_level} - "
        f"{operation_label} {username} sur {target_service}"
    )
    return subject, html_body


def build_confirmation_email(
    operation_type: str,
    target_service: str,
    user_data: dict,
) -> tuple[str, str, str]:
    """Build the end-user confirmation email sent after full approval.

    For CREATE_USER the email includes the credentials.

    Returns:
        (recipient_email, subject, html_body)
    """
    username = user_data.get("username") or "N/A"
    password = user_data.get("password") or "N/A"
    email = user_data.get("email") or "N/A"

    service_label = SERVICE_LABELS.get(target_service.lower(), target_service)
    operation_label = OPERATION_LABELS.get(operation_type, operation_type)
    header_color = {
        "CREATE_USER": "#27ae60",
        "UPDATE_USER": "#2980b9",
        "DELETE_USER": "#e74c3c",
    }.get(operation_type, "#2c3e50")

    if operation_type == "CREATE_USER":
        body_content = f"""
    <p>Votre compte a ete cree avec succes sur le service <strong>{service_label}</strong>.</p>
    <div class="info-box">
      <p><strong>Service :</strong> {service_label}</p>
      <p><strong>Identifiant :</strong> {username}</p>
      <p><strong>Mot de passe :</strong> {password}</p>
      <p><strong>Email :</strong> {email}</p>
    </div>
    <div class="warning">
      Pour des raisons de securite, nous vous recommandons de changer votre mot de passe lors de votre premiere connexion.
    </div>"""
    elif operation_type == "UPDATE_USER":
        body_content = f"""
    <p>Votre compte sur le service <strong>{service_label}</strong> a ete mis a jour avec succes.</p>
    <div class="info-box">
      <p><strong>Service :</strong> {service_label}</p>
      <p><strong>Identifiant :</strong> {username}</p>
      <p><strong>Email :</strong> {email}</p>
    </div>
    <p>Si vous n'etes pas a l'origine de cette modification, contactez votre administrateur.</p>"""
    else:  # DELETE_USER
        body_content = f"""
    <p>Votre compte sur le service <strong>{service_label}</strong> a ete supprime.</p>
    <div class="info-box">
      <p><strong>Service :</strong> {service_label}</p>
      <p><strong>Identifiant :</strong> {username}</p>
    </div>
    <p>Si vous pensez que cette suppression est une erreur, contactez votre administrateur.</p>"""

    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: Arial, sans-serif; background: #f4f4f4; margin: 0; padding: 20px; }}
    .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
    .header {{ background: {header_color}; color: white; padding: 20px 30px; }}
    .header h1 {{ margin: 0; font-size: 20px; }}
    .content {{ padding: 30px; }}
    .info-box {{ background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 6px; padding: 20px; margin: 20px 0; }}
    .info-box p {{ margin: 8px 0; }}
    .info-box strong {{ color: #2c3e50; }}
    .warning {{ background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; padding: 15px; margin: 20px 0; color: #856404; }}
    .footer {{ background: #f8f9fa; padding: 15px 30px; text-align: center; color: #888; font-size: 12px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>Gateway IAM - {operation_label} de compte {service_label}</h1>
    </div>
    <div class="content">
      <p>Bonjour <strong>{username}</strong>,</p>
      {body_content}
    </div>
    <div class="footer">Gateway IAM Provisioning System</div>
  </div>
</body>
</html>"""

    subject = f"[Gateway IAM] {operation_label} de compte - {service_label}"
    return email, subject, html_body


def build_decision_result_page(
    title: str,
    message: str,
    success: bool = True,
) -> str:
    """Small HTML page shown in the browser after an approver clicks a decision link."""
    color = "#27ae60" if success else "#e74c3c"
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Gateway IAM</title>
  <style>
    body {{ font-family: Arial, sans-serif; background: #f4f4f4; margin: 0; padding: 40px 20px; }}
    .container {{ max-width: 500px; margin: 0 auto; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
    .header {{ background: {color}; color: white; padding: 20px 30px; }}
    .header h1 {{ margin: 0; font-size: 20px; }}
    .content {{ padding: 30px; color: #333; }}
    .footer {{ background: #f8f9fa; padding: 15px 30px; text-align: center; color: #888; font-size: 12px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header"><h1>{title}</h1></div>
    <div class="content"><p>{message}</p></div>
    <div class="footer">Gateway IAM Provisioning System</div>
  </div>
</body>
</html>"""
