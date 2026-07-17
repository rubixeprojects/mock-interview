from django.shortcuts import redirect

def login_required(view_func):
    def wrapper(request, *args, **kwargs):
        if "user_email" not in request.session:
            return redirect("home")
        return view_func(request, *args, **kwargs)
    return wrapper
