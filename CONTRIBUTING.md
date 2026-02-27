# Contributing to Lambda Durable Functions vs Step Functions Comparison

Thank you for your interest in contributing! This document provides guidelines for contributing to this project.

## How to Contribute

### Reporting Issues

- Use GitHub Issues to report bugs or suggest enhancements
- Check existing issues before creating a new one
- Provide detailed information:
  - Steps to reproduce (for bugs)
  - Expected vs actual behavior
  - AWS region and service versions
  - Relevant logs or error messages

### Submitting Changes

1. **Fork the repository**
2. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Make your changes**
   - Follow existing code style
   - Add tests if applicable
   - Update documentation
4. **Test your changes**
   ```bash
   # Run unit tests
   python -m pytest tests/
   
   # Test deployment
   sam build && sam deploy
   ```
5. **Commit your changes**
   ```bash
   git commit -m "Add feature: description"
   ```
6. **Push to your fork**
   ```bash
   git push origin feature/your-feature-name
   ```
7. **Submit a Pull Request**

### Pull Request Guidelines

- Provide a clear description of the changes
- Reference any related issues
- Ensure all tests pass
- Update README.md if needed
- Keep changes focused and atomic

## Code Style

### Python

- Follow PEP 8 style guide
- Use type hints where appropriate
- Add docstrings for functions and classes
- Keep functions small and focused

### Infrastructure as Code

- Use AWS SAM for Lambda and Step Functions
- Follow AWS best practices
- Add comments for complex configurations
- Use descriptive resource names

### Documentation

- Use Markdown for documentation
- Keep language clear and concise
- Include code examples where helpful
- Update table of contents if adding sections

## Testing

### Unit Tests

- Write tests for new functionality
- Maintain or improve code coverage
- Use pytest for Python tests
- Mock AWS services in tests

### Integration Tests

- Test against real AWS services when possible
- Use separate test AWS account
- Clean up resources after testing
- Document test setup requirements

## Cost Considerations

When contributing changes that affect AWS costs:

- Document cost implications
- Provide cost estimates if possible
- Consider free tier limits
- Test with small volumes first

## Experiment Reproducibility

If modifying the experiment:

- Document methodology changes
- Maintain data integrity
- Update cost calculations
- Preserve original results for comparison

## Questions?

- Open a GitHub Issue for questions
- Tag with "question" label
- Check existing issues first

## Code of Conduct

- Be respectful and inclusive
- Focus on constructive feedback
- Help others learn and grow
- Follow GitHub's Community Guidelines

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

Thank you for contributing! 🎉
