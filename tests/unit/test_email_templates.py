"""Unit tests for the approval email template builders"""
from app.services.email_templates import (
    build_approval_email,
    build_confirmation_email,
    build_decision_result_page,
    compute_warnings,
    filter_roles_for_service,
)


class TestFilterRolesForService:
    def test_keeps_service_specific_roles(self):
        roles = ["mysql-admin", "odoo-user", "postgresql-readonly"]
        assert filter_roles_for_service(roles, "MYSQL") == ["mysql-admin"]

    def test_keeps_generic_roles(self):
        roles = ["readonly", "odoo-user"]
        assert filter_roles_for_service(roles, "MYSQL") == ["readonly"]

    def test_fallback_to_all_roles_when_no_match(self):
        roles = ["odoo-user", "ldap-group"]
        assert filter_roles_for_service(roles, "MYSQL") == roles

    def test_empty_roles(self):
        assert filter_roles_for_service([], "MYSQL") == []


class TestComputeWarnings:
    def test_intern_with_admin_role(self):
        warnings = compute_warnings(
            "CREATE_USER",
            {
                "email": "a@b.c",
                "roles": ["mysql-admin"],
                "attributes": {"description": "Stagiaire en informatique", "title": "Dev"},
            },
        )
        assert any("stagiaire" in w for w in warnings)

    def test_multiple_admin_roles(self):
        warnings = compute_warnings(
            "CREATE_USER",
            {
                "email": "a@b.c",
                "roles": ["mysql-admin", "odoo-admin"],
                "attributes": {"description": "x", "title": "y"},
            },
        )
        assert any("Roles admin sur plusieurs services" in w for w in warnings)

    def test_delete_admin_account(self):
        warnings = compute_warnings(
            "DELETE_USER",
            {"email": "a@b.c", "roles": ["admin"], "attributes": {"description": "x"}},
        )
        assert any("Suppression d'un compte avec privileges admin" in w for w in warnings)

    def test_create_without_email(self):
        warnings = compute_warnings(
            "CREATE_USER",
            {"email": "", "roles": [], "attributes": {"description": "x"}},
        )
        assert any("sans adresse email" in w for w in warnings)

    def test_no_description_or_title(self):
        warnings = compute_warnings(
            "CREATE_USER",
            {"email": "a@b.c", "roles": [], "attributes": {}},
        )
        assert any("Aucune description ou poste" in w for w in warnings)

    def test_many_roles(self):
        warnings = compute_warnings(
            "CREATE_USER",
            {
                "email": "a@b.c",
                "roles": ["r1", "r2", "r3", "r4", "r5"],
                "attributes": {"description": "x"},
            },
        )
        assert any("Nombre eleve de roles (5)" in w for w in warnings)

    def test_update_without_changes(self):
        warnings = compute_warnings(
            "UPDATE_USER",
            {"email": "a@b.c", "roles": [], "attributes": {"description": "x"}},
            changes=[],
        )
        assert any("sans changement visible" in w for w in warnings)

    def test_update_password_change(self):
        warnings = compute_warnings(
            "UPDATE_USER",
            {"email": "a@b.c", "roles": [], "attributes": {"description": "x"}},
            changes=[{"field": "Mot de passe", "old": "(ancien)", "new": "(modifie)"}],
        )
        assert any("Changement de mot de passe" in w for w in warnings)

    def test_update_admin_roles_change(self):
        warnings = compute_warnings(
            "UPDATE_USER",
            {"email": "a@b.c", "roles": ["admin"], "attributes": {"description": "x"}},
            changes=[{"field": "Roles", "old": "a", "new": "b"}],
        )
        assert any("Modification de roles avec privileges admin" in w for w in warnings)

    def test_clean_request_no_warnings(self):
        warnings = compute_warnings(
            "CREATE_USER",
            {
                "email": "a@b.c",
                "roles": ["mysql-readonly"],
                "attributes": {"description": "Employe permanent", "title": "Dev"},
            },
        )
        assert warnings == []


class TestBuildApprovalEmail:
    def _build(self, **overrides):
        params = dict(
            operation_id="op-123",
            operation_type="CREATE_USER",
            target_service="MYSQL",
            user_data={
                "username": "jdoe",
                "email": "jdoe@example.com",
                "first_name": "John",
                "last_name": "Doe",
                "roles": ["mysql-admin"],
                "attributes": {"description": "Dev"},
            },
            approver_name="Manager",
            approver_level=1,
            total_approvers=2,
            approve_url="http://gw/decision?token=t&approved=true",
            reject_url="http://gw/decision?token=t&approved=false",
        )
        params.update(overrides)
        return build_approval_email(**params)

    def test_subject_format(self):
        subject, _ = self._build()
        assert subject == "[Gateway IAM] Approbation Niveau 1 - Creation jdoe sur MYSQL"

    def test_contains_buttons_and_urls(self):
        _, html = self._build()
        assert "APPROUVER" in html and "REJETER" in html
        assert "approved=true" in html and "approved=false" in html

    def test_level_banner_for_multilevel(self):
        _, html = self._build()
        assert "Approbation niveau 1/2" in html
        assert "premier approbateur" in html

    def test_level_banner_for_second_level(self):
        _, html = self._build(approver_level=2)
        assert "Les niveaux precedents ont approuve" in html

    def test_no_level_banner_single_approver(self):
        _, html = self._build(total_approvers=1)
        assert "Approbation niveau" not in html

    def test_update_changes_table(self):
        _, html = self._build(
            operation_type="UPDATE_USER",
            changes=[{"field": "Email", "old": "a@b.c", "new": "x@y.z"}],
        )
        assert "Modifications apportees" in html
        assert "a@b.c" in html and "x@y.z" in html

    def test_update_without_changes_shows_fallback(self):
        _, html = self._build(operation_type="UPDATE_USER", changes=None)
        assert "Aucun detail de modification disponible" in html


class TestBuildConfirmationEmail:
    def test_create_includes_credentials(self):
        to, subject, html = build_confirmation_email(
            "CREATE_USER",
            "mysql",
            {"username": "jdoe", "password": "s3cret", "email": "jdoe@example.com"},
        )
        assert to == "jdoe@example.com"
        assert subject == "[Gateway IAM] Creation de compte - MySQL"
        assert "s3cret" in html
        assert "changer votre mot de passe" in html

    def test_update_no_credentials(self):
        _, subject, html = build_confirmation_email(
            "UPDATE_USER",
            "postgresql",
            {"username": "jdoe", "password": "s3cret", "email": "jdoe@example.com"},
        )
        assert "Modification" in subject
        assert "s3cret" not in html

    def test_delete_confirmation(self):
        _, subject, html = build_confirmation_email(
            "DELETE_USER",
            "ldap",
            {"username": "jdoe", "email": "jdoe@example.com"},
        )
        assert "Suppression" in subject
        assert "a ete supprime" in html


class TestDecisionResultPage:
    def test_success_page(self):
        html = build_decision_result_page("Titre", "Message ok")
        assert "Titre" in html and "Message ok" in html
        assert "#27ae60" in html

    def test_failure_page(self):
        html = build_decision_result_page("Erreur", "Echec", success=False)
        assert "#e74c3c" in html
