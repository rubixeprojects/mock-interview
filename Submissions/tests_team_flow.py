from django.test import TestCase
from Submissions.models import StudentIdentity, Student, Team
from Submissions.views import _post_login_redirect

class TeamRedirectionTests(TestCase):
    def setUp(self):
        self.ident = StudentIdentity.objects.create(email="test@example.com", name="Test Student")
        self.course = "CDS"
        self.enrollment = Student.objects.create(identity=self.ident, course=self.course)

    def test_no_team_no_preference(self):
        """No team and no preference record -> team_preference"""
        self.assertEqual(_post_login_redirect(self.enrollment), "team_preference")

    def test_joined_team_no_id(self):
        """Joined a team (invitee) but no team_id assigned -> team_waiting"""
        owner = StudentIdentity.objects.create(email="owner@example.com", name="Owner")
        team = Team.objects.create(student=owner, course=self.course, team_id=None)
        self.enrollment.team = team
        self.enrollment.save()
        self.assertEqual(_post_login_redirect(self.enrollment), "team_waiting")

    def test_joined_team_with_id(self):
        """Joined a team (invitee) with team_id assigned -> pick_projects"""
        owner = StudentIdentity.objects.create(email="owner@example.com", name="Owner")
        team = Team.objects.create(student=owner, course=self.course, team_id="PTID-CDS-001")
        self.enrollment.team = team
        self.enrollment.save()
        self.assertEqual(_post_login_redirect(self.enrollment), "pick_projects")

    def test_creator_no_id(self):
        """Created a team preference but no team_id assigned -> team_waiting"""
        # Note: In the current flow, creator's enrollment.team might not be set yet,
        # but a Team record exists where they are the student (creator).
        Team.objects.create(student=self.ident, course=self.course, team_id=None)
        self.assertEqual(_post_login_redirect(self.enrollment), "team_waiting")

    def test_creator_with_id(self):
        """Created a team preference and team_id assigned -> pick_projects"""
        Team.objects.create(student=self.ident, course=self.course, team_id="PTID-CDS-002")
        self.assertEqual(_post_login_redirect(self.enrollment), "pick_projects")
