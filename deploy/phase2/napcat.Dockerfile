FROM mlikiowa/napcat-docker@sha256:132448a544ed5cfa948dff8ae19e80bf4e17896e8ab2ba1c6cbcd86f8ad3e56e
# Preserve the pinned official QQ binary and replace only the untouched,
# checksum-verified official NapCat release archive. Never publish this image.
RUN test ! -e /app/napcat/napcat.mjs
COPY NapCat.Shell.zip /app/NapCat.Shell.zip
RUN echo "735f2e633247df3ffd6e8ee8b84d7039c315b2655f5807e5f0029f93d2c30016  /app/NapCat.Shell.zip" | sha256sum -c -
